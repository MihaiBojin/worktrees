"""An append-only record of what each run did.

One JSON object per line, in `audit-YYYY-MM.jsonl` by UTC month under the
user's state directory. A line is one `write` on a descriptor opened with
`O_APPEND`, so runs side by side interleave whole lines and nothing here ever
seeks, truncates, renames or deletes: retention is the reader's to decide.

One `write` can still come back short, on a full disk or at a file size
limit, and what it wrote is then a fragment nothing can take back. The rest
of that line is never written. So that no later line is lost to it, each
append takes the file's lock, looks at the last byte and begins with a
newline of its own when that byte is not one; the line that does carries
`torn`. The look and the write are under one lock, so it holds for a run
that already had the file open when another run tore a line. The fragment
is the only line lost, and a reader skips it as it would any line that is
not JSON.

A log that cannot be written is one warning on stderr and the command goes
on: a full disk must not be what stops a person removing a worktree.
"""

from __future__ import annotations

import contextlib
import os
import sys
import threading
import time
from collections.abc import Callable, Sequence
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
        "failure",
        "fd",
        "lock",
        "month",
        "on",
        "run",
        "seq",
        "started",
    )

    def __init__(self) -> None:
        self.on: frozenset[str] | None = None
        self.run = ""
        self.seq = 0
        self.began = 0.0
        self.base: dict = {}
        self.error = ""
        self.failure = ""
        self.fd = -1
        self.month = ""
        self.lock = threading.Lock()
        # When `cli.main` was entered, so a run's time counts its parsing.
        self.started = 0.0


_state = _State()


def active() -> bool:
    return _state.on is not None


def begun() -> bool:
    """Whether this run has started recording, even if the log failed since."""
    return bool(_state.run)


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
        # A message built for a terminal carries its colour; the log does not.
        return scrub(_plain(value))
    if isinstance(value, dict):
        return {scrub(str(k)): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return [clean(v) for v in sorted(value, key=str)]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, os.PathLike):
        return scrub(os.fspath(value))
    # No catch-all: `str()` of anything else would put a Python repr in the
    # log for a reader to parse. The event is dropped and the caller warned.
    raise TypeError(f"{type(value).__name__} has no place in an audit event")


def _plain(text: str) -> str:
    """A message without the colour codes a terminal run painted into it."""
    if "\x1b" not in text:
        return text
    import re

    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _clip(text: str) -> tuple[str, int]:
    """The start of `text`, and how many characters were left off."""
    text = scrub(text.strip())
    return text[:_CLIP], max(0, len(text) - _CLIP)


def _clipped(fields: dict, name: str, text: str) -> None:
    """Put `text` under `name`, and beside it the count that was left off, so
    no reader has to recognise a truncation mark inside the text."""
    fields[name], fields[f"{name}Dropped"] = _clip(text)


# How a command that did not exit 0 ended, apart from what it said.
FAILURES = ("usage", "refused", "git", "interrupted", "crash", "result")


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
    _state.error = _state.failure = ""
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


def failed(message: str, kind: str) -> None:
    """Keep the kind of refusal or failure a command ended with, one of
    `FAILURES`, and what it said, for its last line."""
    _state.error, _state.failure = _plain(message), kind


def end(code: int | None) -> None:
    """Record how the command ended and stop recording."""
    if active():
        # `result`: it ran to its end and its answer is the exit code, as a
        # stopped rebase's is. `crash`: it left by an exception nothing caught.
        failure = (
            None
            if code == 0
            else _state.failure or ("crash" if code is None else "result")
        )
        fields: dict = {"code": code, "ms": _since(_state.began), "failure": failure}
        if _state.error:
            _clipped(fields, "error", _state.error)
        record("invocation.end", **fields)
    _state.on = None
    _state.run, _state.error, _state.failure, _state.started = "", "", "", 0.0
    if _state.fd >= 0:
        with contextlib.suppress(OSError):
            os.close(_state.fd)
    _state.fd, _state.month = -1, ""


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
            _clipped(fields, "out", out)
        if err.strip():
            _clipped(fields, "err", err)
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
            row = clean(
                {
                    "v": SCHEMA,
                    "ts": f"{stamp}.{int(now % 1 * 1000):03d}Z",
                    "run": _state.run,
                    "seq": _state.seq,
                    "event": event,
                    **_state.base,
                    "cwd": cwd,
                    **fields,
                }
            )

            fixed = row if isinstance(row, dict) else {}

            def line(torn: bool) -> bytes:
                # `torn`: this line closes a fragment of an event that was lost.
                whole = {**fixed, "torn": True} if torn else fixed
                return json.dumps(whole, separators=(",", ":")).encode("ascii")

            _append(line)
        except TypeError as exc:
            # A field of a type no reader was promised: this event is not
            # written, the rest of the run still is, and the fault is said.
            _warn_event(event, exc)
        except OSError as exc:
            # One warning, then nothing more is tried: a log that failed once
            # would fail on each of the forty commands after it.
            _state.on = None
            _warn(path, exc)


def _open(path: Path, month: str) -> None:
    """Hold the month's file open for appending."""
    if _state.fd >= 0 and _state.month == month:
        return
    if _state.fd >= 0:
        os.close(_state.fd)
        _state.fd = -1
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Readable as well, for the one byte `_open_ended` looks at.
    _state.fd = os.open(
        path,
        os.O_RDWR | os.O_APPEND | os.O_CREAT | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    _state.month = month


# How long an append waits for the file's lock before it goes without one.
_LOCK_TRIES = 50
_LOCK_PAUSE = 0.002


def _lock(fd: int) -> bool:
    """Take the file's lock, or say it could not be had.

    Never waits long: the lock is held for one `write`, and a log must not be
    what holds a command up. False where there is no `flock`, on a filesystem
    that refuses it, or when another process keeps it past the wait.
    """
    try:
        import fcntl
    except ImportError:
        return False
    for _ in range(_LOCK_TRIES):
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            time.sleep(_LOCK_PAUSE)
        except OSError:
            return False
    return False


def _unlock(fd: int) -> None:
    import fcntl

    with contextlib.suppress(OSError):
        fcntl.flock(fd, fcntl.LOCK_UN)


def _open_ended(fd: int) -> bool:
    """Whether the file ends in bytes with no newline after them.

    Every complete line ends in one and is one `write`, so a last byte that
    is anything else is the tail of a line some run could not finish.
    """
    try:
        size = os.fstat(fd).st_size
        return bool(size) and os.pread(fd, 1, size - 1) != b"\n"
    except (AttributeError, OSError):
        return False


def _append(line: Callable[[bool], bytes]) -> None:
    """Write one line in one `write`, after whatever the file ends in.

    Under the lock the last byte is known for as long as the write takes, in
    every process that appends here, so a fragment is closed by the next line
    whoever left it and whenever. Without the lock nothing is known for that
    long, so the line brings a newline of its own whatever the file ends in,
    and a reader meets an empty line it skips.
    """
    fd = _state.fd
    locked = _lock(fd)
    try:
        torn = _open_ended(fd)
        data = (b"\n" if torn or not locked else b"") + line(torn) + b"\n"
        wrote = os.write(fd, data)
        if wrote == len(data):
            return
        # Short: what went out is a fragment and its rest is never written.
        # Close it here if a newline still fits; if not, the next append
        # finds the open end and closes it.
        with contextlib.suppress(OSError):
            os.write(fd, b"\n")
        import errno

        raise OSError(errno.EIO, f"short write, {wrote} of {len(data)} bytes")
    finally:
        if locked:
            _unlock(fd)


def _warn(path: Path, exc: OSError) -> None:
    from . import progress, render

    reason = exc.strerror or str(exc)
    progress.line(
        render.warn(
            f"audit log not written: {path}: {reason}\n"
            f"this command goes on unrecorded; {ENV}=off turns the log off"
        )
    )


def _warn_event(event: str, exc: TypeError) -> None:
    from . import progress, render

    progress.line(render.warn(f"audit event {event} not written: {exc}"))
