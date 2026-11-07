"""Choosing one worktree out of those this repository has.

No fzf. Typing filters the list with the matching `gw list QUERY` uses, and
the arrow keys move through what is left, so a repository with forty
worktrees narrows to one in a few keys and costs no dependency, no spawn and
no absent-fzf fallback.
"""

from __future__ import annotations

import os
import select
import sys
import termios
import tty
from collections.abc import Iterator, Sequence

from . import layout, render
from .git import Refused
from .repo import Worktree


def subsequence(query: str, text: str) -> tuple[int, int] | None:
    """The one idea worth taking from fzf: the query's letters in order.

    Returns how tightly they cluster and where they start, so `tst` finds
    `add-tests`. None when they do not all appear.
    """
    q, t = query.lower(), text.lower()
    first: int | None = None
    last = seen = 0
    for i, ch in enumerate(t):
        if seen < len(q) and ch == q[seen]:
            if first is None:
                first = i
            last, seen = i, seen + 1
    if seen < len(q) or first is None:
        return None
    return last - first, first


def matches(query: str, worktrees: Sequence[Worktree]) -> list[Worktree]:
    """Substring first, then subsequence, each group in its own order.

    A query that is exactly one worktree's name, branch or path is that
    worktree alone, so `one` does not also offer `one-more`. Otherwise a
    substring hit always beats a subsequence one, so typing more of a name
    never moves it down the list.

    Substring looks at the branch and the path, which holds the worktree's
    name; subsequence looks at the branch and the name. Every path here
    contains `.worktrees`, which supplies a `t`, a `w` and an `o` to any query
    that wants them, so a loose match against the whole path finds everything
    and means nothing.
    """
    if not query:
        return list(worktrees)
    exactly = [
        wt
        for wt in worktrees
        if query in (wt.label, wt.branch, wt.path, layout.name_of(wt.path))
    ]
    if len(exactly) == 1:
        return exactly
    q = query.lower()
    exact: list[tuple[int, Worktree]] = []
    loose: list[tuple[int, Worktree]] = []
    for wt in worktrees:
        label, path = wt.label.lower(), wt.path.lower()
        if q in label:
            exact.append((label.index(q), wt))
            continue
        if q in path:
            exact.append((len(label) + path.index(q), wt))
            continue
        ranks = [
            r
            for r in (
                subsequence(query, wt.label),
                subsequence(query, layout.name_of(wt.path)),
            )
            if r is not None
        ]
        if ranks:
            loose.append((min(ranks)[0], wt))
    return [wt for _, wt in exact] + [wt for _, wt in loose]


# Key presses by name. Any other one-character string is typed into the filter.
UP, DOWN, PICK, ESC, ERASE, CANCEL = "up", "down", "pick", "esc", "erase", "cancel"


def _narrow(
    query: str,
    candidates: Sequence[Worktree],
    shown: Sequence[tuple[str, Worktree]],
) -> tuple[list[tuple[str, Worktree]], list[Worktree]]:
    """The rows a filter leaves, matched together so the exact-name rule sees
    every worktree on the screen, then split back into shown and offered."""
    kept = matches(query, [*(wt for _, wt in shown), *candidates])
    return (
        [(mark, wt) for mark, wt in shown if wt in kept],
        [wt for wt in kept if wt in candidates],
    )


def choose(
    candidates: Sequence[Worktree],
    keys: Iterator[str] | None = None,
    outright: bool = True,
    shown: Sequence[tuple[str, Worktree]] = (),
    at: Worktree | None = None,
    here: Worktree | None = None,
    query: str = "",
) -> Worktree | None:
    """Ask which one, and None means cancelled.

    Typing filters the list and backspace takes a letter back. The arrow keys
    move the highlight and enter takes it. Esc clears the filter, and on an
    empty filter it cancels, as ctrl-d does. `keys` is those presses by name,
    and a test passes them rather than a terminal.

    `query` is the filter the list opens with: `gw list QUERY` passes its
    own, so one esc widens the list to every worktree.

    `outright` says whether one match is taken without asking. A query that
    narrows to one has already said which, so it is. No query at all is a
    request to be shown the options, and being moved without being asked
    because there happened to be one other worktree is not that.

    `shown` are drawn dimmed above the offered ones, each after its mark,
    and the highlight never lands on them: the worktree `gw list` stands in,
    the main checkout `gwr` cannot remove. A list that leaves them out shows
    fewer rows than `git worktree list`, which reads as though something went
    missing. `at` is the candidate highlighted first, and `here` is one that
    can be picked and is also where you stand, marked `*` after its name.

    A run whose stdin is not a terminal is refused rather than left to block:
    an agent or a pipe reaching a prompt would hang, and --json answers the
    same question without one.
    """
    offered = _narrow(query, candidates, shown)[1]
    if not offered:
        return None
    if outright and len(offered) == 1:
        return offered[0]
    if keys is not None:
        return _select(candidates, keys, shown, at, here, query)

    if not sys.stdin.isatty():
        count = len(offered)
        noun = "worktree" if count == 1 else "worktrees"
        raise Refused(
            f"{count} {noun} to choose from and this is not a terminal; "
            "narrow the query, or use --list or --json"
        )
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    # cbreak rather than raw: keys arrive one at a time and unechoed, and
    # ctrl-c still raises KeyboardInterrupt. TCSANOW, because the default
    # TCSAFLUSH throws away whatever was typed before the list appeared.
    tty.setcbreak(fd, termios.TCSANOW)
    try:
        return _select(candidates, _keys(fd), shown, at, here, query)
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, saved)


def _select(
    candidates: Sequence[Worktree],
    keys: Iterator[str],
    shown: Sequence[tuple[str, Worktree]],
    first: Worktree | None,
    here: Worktree | None,
    query: str,
) -> Worktree | None:
    """Draw the list on stderr, follow the keys, and return the pick.

    On a terminal that can move its cursor, every key redraws the list in
    place. Anywhere else, a dumb terminal or a stderr that is not one, the
    list is printed once, each move prints the row it lands on, and each
    change to the filter prints the rows it leaves.
    """
    redraw = sys.stderr.isatty() and os.environ.get("TERM") != "dumb"

    def cells(mark: str, wt: Worktree) -> list[str]:
        branch = wt.label + (" *" if wt is here else "")
        return [mark, layout.name_of(wt.path), branch, wt.path]

    header = ["", "WORKTREE", "BRANCH", "PATH"]
    # Over every row the filter could leave, so the columns hold still as
    # the list narrows.
    every = [header, *(cells(m, wt) for m, wt in shown)]
    every += [cells(">", wt) for wt in candidates]
    widths = [max(len(c[i]) for c in every) for i in range(3)]

    # The climb counts lines, so where the list is redrawn the frame has to
    # fit the screen: every row is cut to the terminal's width, less the last
    # column, where some terminals wrap early, and the rows shown are a
    # window as tall as the screen allows that follows the highlight.
    room: int | None = None
    height: int | None = None
    if redraw:
        try:
            size = os.get_terminal_size(sys.stderr.fileno())
            room, height = size.columns - 1, size.lines
        except (OSError, ValueError):
            pass
    top = 0

    def line(texts: list[str], codes: list[str]) -> str:
        # Padded and cut before it is painted, or the escape codes count as
        # width and every column under them sits crooked.
        padded = [t.ljust(w) for t, w in zip(texts, widths, strict=False)]
        padded.append(texts[-1])
        left, out = room, []
        for text, code in zip(padded, codes, strict=True):
            if left is not None:
                if left <= 0:
                    break
                text = text[:left]
                left -= len(text) + 2
            out.append(render.err(text, code))
        return "  ".join(out)

    def row(wt: Worktree, lit: bool) -> str:
        if lit:
            codes = [
                render.BOLD,
                render.BOLD + render.REVERSE,
                render.BRANCH + render.REVERSE,
                render.DIM,
            ]
            return line(cells(">", wt), codes)
        return line(cells("", wt), [render.DIM, render.BOLD, render.BRANCH, render.DIM])

    def frame() -> list[str]:
        nonlocal top
        # The header and the prompt take a line each.
        fits = len(offered) if height is None else max(1, height - 2 - len(seen))
        top = min(max(top, at - fits + 1), at)
        # The rows that cannot be picked stay grey, their worktree name bold.
        dim = [render.DIM, render.BOLD + render.DIM, render.DIM, render.DIM]
        return [
            line(header, [render.BOLD] * 4),
            *(line(cells(m, wt), dim) for m, wt in seen),
            *(row(wt, top + i == at) for i, wt in enumerate(offered[top : top + fits])),
        ]

    def prompt() -> str:
        esc = "clear" if query else "cancel"
        hint = f"[type to filter, up/down and enter, esc to {esc}]"
        return render.ask("which?", hint) + query

    seen, offered = _narrow(query, candidates, shown)
    at = offered.index(first) if first in offered else 0
    drawn = frame()
    sys.stderr.write("".join(f"{text}\n" for text in drawn) + prompt())
    while True:
        sys.stderr.flush()
        key = next(keys, CANCEL)
        if key == PICK and not offered:
            continue
        if key in (PICK, CANCEL) or (key == ESC and not query):
            sys.stderr.write("\n")
            sys.stderr.flush()
            return offered[at] if key == PICK else None
        if key in (UP, DOWN):
            if not offered:
                continue
            at = (at + (1 if key == DOWN else -1)) % len(offered)
            if not redraw:
                sys.stderr.write(f"\n{row(offered[at], True)}\n{prompt()}")
                continue
        else:
            if key == ESC:
                query = ""
            elif key == ERASE and query:
                query = query[:-1]
            elif len(key) == 1:
                query += key
            else:
                continue
            seen, offered = _narrow(query, candidates, shown)
            at = 0
        if redraw:
            # The cursor rests at the end of the prompt, so climb back over
            # the frame drawn last, which the filter may have made longer or
            # shorter, and erase everything under it before drawing again.
            climb = f"\r\033[{len(drawn)}A\033[J"
            drawn = frame()
            sys.stderr.write(climb + "".join(f"{t}\n" for t in drawn) + prompt())
        else:
            drawn = frame()
            sys.stderr.write("\n" + "".join(f"{t}\n" for t in drawn) + prompt())


def _keys(fd: int) -> Iterator[str]:
    """Key presses by name, read one byte at a time from a cbreak terminal.

    An arrow is `ESC [ A` or, in a terminal's application mode, `ESC O A`.
    Any other `ESC [` sequence is read to its final byte and dropped, so the
    `~` that ends the delete key is not typed into the filter. An esc on its
    own is told apart by nothing following it within 50ms, since a terminal
    writes a whole sequence at once; a byte that follows it and starts no
    sequence is a key of its own, so two escapes arriving together are two.
    Printable ASCII is typed into the filter.
    """
    arrows = {b"A": UP, b"B": DOWN}
    pending = b""
    while True:
        byte, pending = (pending or os.read(fd, 1)), b""
        if byte in (b"", b"\x04"):
            yield CANCEL
        elif byte in (b"\r", b"\n"):
            yield PICK
        elif byte in (b"\x7f", b"\x08"):
            yield ERASE
        elif byte == b"\x1b":
            if not select.select([fd], [], [], 0.05)[0]:
                yield ESC
                continue
            lead = os.read(fd, 1)
            if lead == b"O":
                key = arrows.get(os.read(fd, 1))
            elif lead == b"[":
                final = os.read(fd, 1)
                while final and not 0x40 <= final[0] <= 0x7E:
                    final = os.read(fd, 1)
                key = arrows.get(final)
            else:
                yield ESC
                pending = lead
                continue
            if key is not None:
                yield key
        elif 0x20 <= byte[0] <= 0x7E:
            yield byte.decode()
