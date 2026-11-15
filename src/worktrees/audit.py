"""An append-only record of what each run did.

One JSON object per line, in `audit-YYYY-MM.jsonl` by UTC month under the
user's state directory. A line is one `write` on a descriptor opened with
`O_APPEND`, so runs side by side interleave whole lines and nothing here ever
seeks, truncates, renames or deletes: retention is the reader's to decide.

One `write` can still come back short, on a full disk or at a file size
limit, and what it wrote is then a fragment nothing can take back. The rest
of that line is never written, since another run may have appended in
between. The fragment is closed with a newline when one can be written; when
it cannot, the next run to open the file finds a last byte that is not a
newline, starts its first line with one and marks that line `torn`. Either
way the fragment is the only line lost, and a reader skips it as it would
any line that is not JSON.

Recording starts at `begin` and stops at `end`, which `cli.main` calls around
one command. Outside that pair every function here returns at once, so code
that imports the package and calls a git spec records nothing.

A log that cannot be written is one warning on stderr and the command goes
on: a full disk must not be what stops a person removing a worktree.
"""

from __future__ import annotations

import contextlib
import os
import sys
import threading
import time
from collections.abc import Sequence
from pathlib import Path

ENV = "WORKTREES_AUDIT"
DIR = "WORKTREES_AUDIT_DIR"

# What a process record is filed under. An invocation and the events that say
# what was removed or moved are recorded whenever the log is on at all.
CATEGORIES = ("reads", "mutating", "forge")

SCHEMA = 1

# A rebase or a forge error can print pages; the log keeps the start of each.
_CLIP = 4000


class Invalid(ValueError):
    """`WORKTREES_AUDIT` holds something other than what it takes."""


def settings(value: str) -> frozenset[str] | None:
    """The categories to record, or None when the log is off.

    Unset or empty records everything. `off` records nothing. Otherwise a
    comma-separated list of `category=on` or `category=off`, and a category
    not named stays on, so `reads=off` is the whole of the usual request.
    """
    text = value.strip().lower()
    if text in ("", "all", "on"):
        return frozenset(CATEGORIES)
    if text == "off":
        return None
    chosen = set(CATEGORIES)
    for part in text.split(","):
        name, _, state = part.strip().partition("=")
        if name not in CATEGORIES or state not in ("on", "off"):
            raise Invalid(
                f"{ENV} takes off, or a comma-separated list of "
                f"{', '.join(c + '=on|off' for c in CATEGORIES)}; got {value!r}"
            )
        (chosen.add if state == "on" else chosen.discard)(name)
    return frozenset(chosen)


def directory() -> Path:
    """Where the monthly files live."""
    stated = os.environ.get(DIR)
    if stated:
        return Path(stated)
    state = os.environ.get("XDG_STATE_HOME", "")
    # The XDG rule: a relative path there is invalid and is ignored.
    base = Path(state) if os.path.isabs(state) else Path.home() / ".local" / "state"
    return base / "worktrees"


class _State:
    __slots__ = (
        "base",
        "began",
        "error",
        "fd",
        "lock",
        "month",
        "on",
        "run",
        "seq",
        "started",
        "unterminated",
    )

    def __init__(self) -> None:
        self.on: frozenset[str] | None = None
        self.run = ""
        self.seq = 0
        self.began = 0.0
        self.base: dict = {}
        self.error = ""
        self.fd = -1
        self.month = ""
        self.lock = threading.Lock()
        # When `cli.main` was entered, so a run's time counts its parsing.
        self.started = 0.0
        # The file ends in a fragment with no newline of its own: the next
        # write begins with one, and the line it writes says so.
        self.unterminated = False


_state = _State()


def active() -> bool:
    return _state.on is not None


def _user() -> str:
    import getpass

    try:
        return getpass.getuser()
    except (OSError, KeyError):
        return ""


def _host() -> str:
    try:
        return os.uname().nodename
    except (AttributeError, OSError):
        return os.environ.get("COMPUTERNAME", "")


def scrub(text: str) -> str:
    """`text` without the user and password a URL in it carries.

    `https://user:token@host/path` becomes `https://host/path`. The scp form
    `git@host:path` is left alone: its user is the account every clone of
    that host names, not a credential.
    """
    if "://" not in text:
        return text
    import re

    return re.sub(r"([A-Za-z][A-Za-z0-9+.-]*://)[^/@\s]+@", r"\1", text)


def clean(value: object) -> object:
    """`value` as JSON can hold it, every string in it scrubbed.

    Run on a whole event as it is serialised, so a URL in a stash subject, a
    commit subject or a failure message is treated as one in an argument is,
    whichever caller built the field.
    """
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, dict):
        return {scrub(str(k)): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return [clean(v) for v in sorted(value, key=str)]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return scrub(str(value))


def _plain(text: str) -> str:
    """A message without the colour codes a terminal run painted into it."""
    if "\x1b" not in text:
        return text
    import re

    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _clip(text: str) -> str:
    text = scrub(text.strip())
    if len(text) <= _CLIP:
        return text
    return text[:_CLIP] + f"… ({len(text) - _CLIP} more characters)"


def arrive() -> None:
    """Note when the command was entered, before its words are parsed."""
    _state.started = time.monotonic()


def begin(prog: str, argv: Sequence[str]) -> None:
    """Start recording one command, as it was typed.

    Called once the words are known not to ask for help: a second call in
    one run, or a call after the log failed, does nothing.
    """
    if _state.run:
        return
    on = settings(os.environ.get(ENV, ""))
    if on is None:
        return
    from . import __version__

    _state.on = on
    _state.run = os.urandom(8).hex()
    _state.seq = 0
    _state.error = ""
    _state.began = _state.started or time.monotonic()
    _state.base = {
        "user": _user(),
        "host": _host(),
        "pid": os.getpid(),
        "version": __version__,
    }
    record(
        "invocation.start",
        argv=[prog, *argv],
        records=sorted(on),
        terminal=sys.stdin.isatty(),
    )


def failed(message: str) -> None:
    """Keep the refusal or failure a command ended with, for its last line."""
    _state.error = _plain(message)


def end(code: int | None) -> None:
    """Record how the command ended and stop recording."""
    if active():
        fields: dict = {"code": code, "ms": _since(_state.began)}
        if _state.error:
            fields["error"] = _clip(_state.error)
        record("invocation.end", **fields)
    _state.on = None
    _state.run, _state.error, _state.started = "", "", 0.0
    if _state.fd >= 0:
        with contextlib.suppress(OSError):
            os.close(_state.fd)
    _state.fd, _state.month = -1, ""
    _state.unterminated = False


def _since(began: float) -> int:
    return round((time.monotonic() - began) * 1000)


def ran(
    tool: str,
    argv: Sequence[str],
    code: int | None,
    began: float,
    *,
    category: str,
    mutates: bool,
    ok: bool,
    out: str = "",
    err: str = "",
) -> None:
    """Record one process: what ran, how long it took and how it ended.

    `began` is `time.monotonic()` from before it started. What it printed is
    kept only for a command that changes something or that failed; a read
    that worked would put every branch and path it listed in the log.
    """
    on = _state.on
    if on is None or category not in on:
        return
    fields: dict = {
        "tool": tool,
        "argv": list(argv),
        "code": code,
        "ok": ok,
        "ms": _since(began),
        "mutates": mutates,
    }
    if mutates or not ok:
        if out.strip():
            fields["out"] = _clip(out)
        if err.strip():
            fields["err"] = _clip(err)
    record("exec", **fields)


def record(event: str, **fields: object) -> None:
    """Append one event. Never raises."""
    if not active():
        return
    import json

    now = time.time()
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now))
    try:
        cwd = os.getcwd()
    except OSError:
        # The directory this process stood in was removed under it.
        cwd = ""
    path = directory() / f"audit-{stamp[:7]}.jsonl"
    with _state.lock:
        if not active():
            return
        try:
            _open(path, stamp[:7])
            _state.seq += 1
            row = {
                "v": SCHEMA,
                "ts": f"{stamp}.{int(now % 1 * 1000):03d}Z",
                "run": _state.run,
                "seq": _state.seq,
                "event": event,
                **_state.base,
                "cwd": cwd,
                **fields,
            }
            if _state.unterminated:
                # This line closes a fragment of an event that was lost.
                row["torn"] = True
            line = json.dumps(clean(row), separators=(",", ":")).encode("ascii")
            _append(line + b"\n")
        except OSError as exc:
            # One warning, then nothing more is tried: a log that failed once
            # would fail on each of the forty commands after it.
            _state.on = None
            _warn(path, exc)


def _open(path: Path, month: str) -> None:
    """Hold the month's file open for appending, and learn how it ends."""
    if _state.fd >= 0 and _state.month == month:
        return
    if _state.fd >= 0:
        os.close(_state.fd)
        _state.fd = -1
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _state.fd = os.open(
        path,
        os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    _state.month = month
    _state.unterminated = _fragment(path)


def _fragment(path: Path) -> bool:
    """Whether the file ends in bytes with no newline after them.

    Every complete line ends in one and is one `write`, so a last byte that
    is anything else is the tail of a line some run could not finish. Two
    runs that both find it both start with a newline, and the reader gets an
    empty line, which it skips as it skips the fragment.
    """
    try:
        with open(path, "rb") as stream:
            size = stream.seek(0, os.SEEK_END)
            if not size:
                return False
            stream.seek(size - 1)
            return stream.read(1) != b"\n"
    except OSError:
        return False


def _append(data: bytes) -> None:
    """Write one line in one `write`, and never finish a line that tore."""
    if _state.unterminated:
        data = b"\n" + data
    wrote = os.write(_state.fd, data)
    if wrote == len(data):
        _state.unterminated = False
        return
    # Short: what went out is a fragment, and another run may already have
    # appended after it, so the rest cannot follow. Close the fragment when a
    # newline still fits; when it does not, the next run to open the file
    # finds the open end and starts with one.
    try:
        _state.unterminated = os.write(_state.fd, b"\n") != 1
    except OSError:
        _state.unterminated = True
    import errno

    raise OSError(errno.EIO, f"short write, {wrote} of {len(data)} bytes")


def _warn(path: Path, exc: OSError) -> None:
    from . import progress, render

    reason = exc.strerror or str(exc)
    progress.line(
        render.warn(
            f"audit log not written: {path}: {reason}\n"
            f"this command goes on unrecorded; {ENV}=off turns the log off"
        )
    )
