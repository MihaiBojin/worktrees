"""A spinner on stderr, and one line per thread saying what it is checking.

Only on a terminal that can clear a line, and never under -v: the verbose
log writes to the lines the spinner redraws.
"""

from __future__ import annotations

import os
import shutil
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager

from . import render

_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
# Keyed by the thread, not its ident: an ident is reused once a thread ends.
_rows: dict[threading.Thread, str] = {}
_lock = threading.Lock()
_active = False
_drawn = 0


def say(text: str) -> None:
    """Replace the calling thread's line. Safe from any thread."""
    with _lock:
        _rows[threading.current_thread()] = text


def _erase() -> str:
    """Back to the first line drawn, then clear to the end of the screen."""
    up = f"\033[{_drawn - 1}A" if _drawn > 1 else ""
    return f"\r{up}\033[J"


def frame(rows: list[str], symbol: str, width: int) -> str:
    """The lines one redraw writes, each cut to fit so none of them wraps.

    A wrapped line would leave the cursor a row lower than `_erase` counts.
    """
    keep = max(width - 3, 0)
    return "\n".join(f"{symbol} {render.err(row[:keep], render.DIM)}" for row in rows)


def line(text: str) -> None:
    """Print a line of stderr without the spinner running through it."""
    global _drawn
    with _lock:
        if _active:
            sys.stderr.write(_erase())
            _drawn = 0
        print(text, file=sys.stderr)


@contextmanager
def running(enabled: bool = True) -> Iterator[None]:
    """Spin while the body runs, and leave no line behind afterwards."""
    global _active, _drawn
    if not enabled or not sys.stderr.isatty() or os.environ.get("TERM", "") == "dumb":
        yield
        return

    stop = threading.Event()

    def spin() -> None:
        global _drawn
        count = 0
        while not stop.wait(0.1):
            width = shutil.get_terminal_size().columns
            symbol = render.err(_FRAMES[count], render.CYAN)
            with _lock:
                # A thread that has finished has nothing left to report.
                for gone in [t for t in _rows if not t.is_alive()]:
                    del _rows[gone]
                rows = [text for text in _rows.values() if text]
                sys.stderr.write(_erase() + frame(rows, symbol, width))
                sys.stderr.flush()
                _drawn = len(rows)
            count = (count + 1) % len(_FRAMES)

    _rows.clear()
    _active = True
    _drawn = 0
    thread = threading.Thread(target=spin, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join()
        with _lock:
            _active = False
            sys.stderr.write(_erase())
            sys.stderr.flush()
            _drawn = 0
            _rows.clear()
