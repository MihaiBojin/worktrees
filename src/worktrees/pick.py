"""Choosing one worktree out of the handful this repository has.

No fzf. The largest number of linked worktrees in one repository here is
four, and at that size a list moved through with the arrow keys reads
faster than a fuzzy finder and costs no dependency, no spawn and no
absent-fzf fallback.
"""

from __future__ import annotations

import os
import select
import sys
import termios
import tty
from collections.abc import Iterator, Sequence

from . import render
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

    A query that is exactly one worktree's name or path is that worktree
    alone, so `one` does not also offer `one-more`. Otherwise a substring hit
    always beats a subsequence one, so typing more of a name never moves it
    down the list.

    Substring looks at the branch and the path; subsequence looks at the
    branch alone. Every path here contains `.worktrees`, which supplies a
    `t`, a `w` and an `o` to any query that wants them, so a loose match
    against the path finds everything and means nothing.
    """
    if not query:
        return list(worktrees)
    exactly = [wt for wt in worktrees if query in (wt.label, wt.branch, wt.path)]
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
        rank = subsequence(query, wt.label)
        if rank is not None:
            loose.append((rank[0], wt))
    return [wt for _, wt in exact] + [wt for _, wt in loose]


UP, DOWN, PICK, CANCEL = "up", "down", "pick", "cancel"


def choose(
    candidates: Sequence[Worktree],
    keys: Iterator[str] | None = None,
    outright: bool = True,
    shown: Sequence[tuple[str, Worktree]] = (),
    at: Worktree | None = None,
    here: Worktree | None = None,
) -> Worktree | None:
    """Ask which one, and None means cancelled.

    The arrow keys move the highlight and enter takes it; a digit takes that
    row outright; esc or ctrl-d cancels. `keys` is those presses by name,
    and a test passes them rather than a terminal.

    `outright` says whether one candidate is taken without asking. A query
    that narrows to one has already said which, so it is. No query at all is
    a request to be shown the options, and being moved without being asked
    because there happened to be one other worktree is not that.

    `shown` are drawn dimmed above the numbered ones, each after its mark,
    and the highlight never lands on them: the worktree `gwl` stands in, the
    main checkout `gwr` cannot remove. A list that leaves them out shows
    fewer rows than `git worktree list`, which reads as though something went
    missing. `at` is the candidate highlighted first, and `here` is one that
    can be picked and is also where you stand, marked `*` after its name.

    A run whose stdin is not a terminal is refused rather than left to block:
    an agent or a pipe reaching a prompt would hang, and --json answers the
    same question without one.
    """
    if not candidates:
        return None
    if outright and len(candidates) == 1:
        return candidates[0]
    first = candidates.index(at) if at in candidates else 0
    if keys is not None:
        return _select(candidates, keys, shown, first, here)

    if not sys.stdin.isatty():
        count = len(candidates)
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
        return _select(candidates, _keys(fd), shown, first, here)
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, saved)


def _select(
    candidates: Sequence[Worktree],
    keys: Iterator[str],
    shown: Sequence[tuple[str, Worktree]],
    at: int,
    here: Worktree | None,
) -> Worktree | None:
    """Draw the list on stderr, follow the keys, and return the pick.

    On a terminal that can move its cursor, every key redraws the list in
    place. Anywhere else, a dumb terminal or a stderr that is not one, the
    list is printed once and each move prints the row it lands on.
    """
    names = {id(wt): wt.label + (" *" if wt is here else "") for wt in candidates}
    width = max(len(name) for name in [*names.values(), *(w.label for _, w in shown)])
    prompt = f"which? [up/down and enter, 1-{len(candidates)}, or esc to cancel] "
    redraw = sys.stderr.isatty() and os.environ.get("TERM") != "dumb"

    def row(i: int) -> str:
        wt = candidates[i]
        mark = f"{'>' if i == at else ' '}{i + 1:>2}"
        style = render.REVERSE if i == at else render.BOLD
        return (
            f"{render.err(mark, render.BOLD if i == at else render.DIM)}  "
            f"{render.err(names[id(wt)].ljust(width), style)}  "
            f"{render.err(wt.path, render.DIM)}"
        )

    # Padded before it is painted, or the escape codes count as width and
    # every column under them sits crooked.
    above = [
        f"{render.err(f'{mark:>3}', render.DIM)}  "
        f"{render.err(wt.label.ljust(width), render.DIM)}  "
        f"{render.err(wt.path, render.DIM)}"
        for mark, wt in shown
    ]
    clear = "\033[2K" if redraw else ""
    lines = [*above, *(row(i) for i in range(len(candidates)))]
    sys.stderr.write("".join(f"{clear}{line}\n" for line in lines) + prompt)
    while True:
        sys.stderr.flush()
        key = next(keys, CANCEL)
        if key in (PICK, CANCEL) or key.isdigit():
            sys.stderr.write("\n")
            sys.stderr.flush()
        if key == PICK:
            return candidates[at]
        if key == CANCEL:
            return None
        if key.isdigit() and 1 <= int(key) <= len(candidates):
            return candidates[int(key) - 1]
        if key.isdigit():
            raise Refused(f"{key} is not one of 1 to {len(candidates)}")
        if key not in (UP, DOWN):
            continue
        at = (at + (1 if key == DOWN else -1)) % len(candidates)
        if redraw:
            # The cursor rests at the end of the prompt, so climb back over
            # the numbered rows and clear each line before writing it again.
            lines = [row(i) for i in range(len(candidates))]
            frame = "".join(f"{clear}{line}\n" for line in lines)
            sys.stderr.write(f"\r\033[{len(lines)}A{frame}{clear}{prompt}")
        else:
            sys.stderr.write(f"\n{row(at)}\n{prompt}")


def _keys(fd: int) -> Iterator[str]:
    """Key presses by name, read one byte at a time from a cbreak terminal.

    An arrow is `ESC [ A` or, in a terminal's application mode, `ESC O A`.
    An esc on its own is a cancel, told apart by nothing following it within
    50ms: a terminal writes the whole arrow sequence at once.
    """
    arrows = {b"A": UP, b"B": DOWN}
    while True:
        byte = os.read(fd, 1)
        if byte in (b"", b"\x04"):
            yield CANCEL
        elif byte in (b"\r", b"\n"):
            yield PICK
        elif byte.isdigit():
            yield byte.decode()
        elif byte == b"\x1b":
            if not select.select([fd], [], [], 0.05)[0]:
                yield CANCEL
            elif os.read(fd, 1) in (b"[", b"O"):
                key = arrows.get(os.read(fd, 1))
                if key is not None:
                    yield key
