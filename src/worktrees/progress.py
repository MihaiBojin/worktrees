"""A spinner on stderr, and one line saying what is being checked.

Only on a terminal that can clear a line, and never under -v: the verbose
log writes to the same line the spinner redraws.
"""

from __future__ import annotations

import os
import shutil
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager

from . import render

_FRAMES = "|/-\\"
_text = ""
_lock = threading.Lock()
_active = False


def say(text: str) -> None:
    """Replace the line under the spinner. Safe from any thread."""
    global _text
    _text = text


def line(text: str) -> None:
    """Print a line of stderr without the spinner running through it."""
    with _lock:
        if _active:
            sys.stderr.write("\r\033[2K")
        print(text, file=sys.stderr)


@contextmanager
def running(enabled: bool = True) -> Iterator[None]:
    """Spin while the body runs, and leave the line blank afterwards."""
    global _active, _text
    if not enabled or not sys.stderr.isatty() or os.environ.get("TERM", "") == "dumb":
        yield
        return

    stop = threading.Event()

    def spin() -> None:
        frame = 0
        while not stop.wait(0.1):
            width = shutil.get_terminal_size().columns
            text = _text[: max(width - 3, 0)]
            with _lock:
                sys.stderr.write(
                    f"\r\033[2K{render.err(_FRAMES[frame], render.CYAN)} "
                    f"{render.err(text, render.DIM)}"
                )
                sys.stderr.flush()
            frame = (frame + 1) % len(_FRAMES)

    _text = ""
    _active = True
    thread = threading.Thread(target=spin, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join()
        with _lock:
            _active = False
            sys.stderr.write("\r\033[2K")
            sys.stderr.flush()
