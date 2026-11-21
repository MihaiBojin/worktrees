"""What every audit event holds, field by field, and a check that one does.

The reference documents these names and types; `tests/test_audit.py` runs
every event a test reads through `check`, so an event with a field nobody
listed, a missing one, or a value of another type fails there and not in
whatever reads the log a month later. Parsing as JSON says none of that.
"""

from __future__ import annotations

import re
from typing import Any

from worktrees import audit, prune

SHA = re.compile(r"[0-9a-f]{40}([0-9a-f]{24})?")
TS = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z")
RUN = re.compile(r"[0-9a-f]{16}")


class Opt:
    """A field that may be absent. Every other field is always present."""

    def __init__(self, spec: Any) -> None:
        self.spec = spec


class Enum:
    def __init__(self, *values: Any) -> None:
        self.values = values


class Match:
    def __init__(self, pattern: re.Pattern[str]) -> None:
        self.pattern = pattern


class Nullable:
    def __init__(self, spec: Any) -> None:
        self.spec = spec


class Either:
    """One of several object shapes, told apart by `key`."""

    def __init__(self, key: str, shapes: dict[str, dict]) -> None:
        self.key, self.shapes = key, shapes


NAT = "a whole number, zero or more"
FREE = "an object whose fields another contract names"

COMMON = {
    "v": Enum(1),
    "ts": Match(TS),
    "run": Match(RUN),
    "seq": NAT,
    "event": str,
    "user": str,
    "host": str,
    "pid": NAT,
    "version": str,
    "cwd": str,
    "torn": Opt(Enum(True)),
}

_ACTION = {"sha": Match(SHA), "argv": [str], "command": str}
ACTION = Either(
    "kind",
    {
        "branch": {**_ACTION, "kind": str, "branch": str},
        "stash": {**_ACTION, "kind": str, "subject": str},
        "reflog-commit": {**_ACTION, "kind": str, "subject": str, "branch": str},
        "previous-tip": {**_ACTION, "kind": str, "of": str, "branch": str},
    },
)

TARGET = {
    "branch": str,
    "sha": str,
    "path": str,
    "reason": str,
    "proof": str,
    "evidence": str,
    "request": Nullable(FREE),
    "permissions": [str],
    "repository": {"commonDir": str, "remote": str, "url": str},
    "head": Nullable({"branch": str, "sha": str}),
    "notices": [str],
    "keepBranch": bool,
    "restore": [ACTION],
    "undo": [str],
    "recovery": str,
}

EVENTS: dict[str, dict] = {
    "invocation.start": {
        "argv": [str],
        "records": [Enum(*audit.CATEGORIES)],
        "terminal": bool,
    },
    "exec": {
        "tool": str,
        "argv": [str],
        "code": Nullable(int),
        "ok": bool,
        "ms": NAT,
        "mutates": bool,
        "out": Opt(str),
        "outDropped": Opt(NAT),
        "err": Opt(str),
        "errDropped": Opt(NAT),
    },
    "invocation.end": {
        "code": Nullable(int),
        "ms": NAT,
        "failure": Nullable(Enum(*audit.FAILURES)),
        "error": Opt(str),
        "errorDropped": Opt(NAT),
    },
    "removal.proposed": {
        **TARGET,
        "deletes": {
            "worktree": str,
            "branch": str,
            "stashes": [Match(SHA)],
            "reflog": [Match(SHA)],
            "ignored": [str],
        },
    },
    "removal.outcome": {
        **TARGET,
        "result": Enum("completed", "partial", "refused"),
        "worktreeRemoved": bool,
        "branchRemoved": bool,
        "stashesDropped": [Match(SHA)],
        "stashesRetained": [Match(SHA)],
        "stashesAbsent": [Match(SHA)],
        "ignoredDeleted": [str],
        "failed": [str],
        "failedStage": Nullable(Enum(*prune.STAGES)),
    },
    "fast-forward.outcome": {
        "result": Enum(
            "current",
            "fast-forwarded",
            "rebased",
            "rotated",
            "conflict",
            "stash-conflict",
        ),
        "branch": str,
        "base": str,
        "sha": str,
        "previous": str,
        "previous_sha": str,
        "ahead": NAT,
        "behind": NAT,
        "proof": str,
        "why": str,
        "boundary": str,
        "carried": NAT,
        "adopted": str,
        "stopped_at": str,
        "subject": str,
        "paths": [str],
        "stash": str,
        "stashes": [FREE],
        "retargeted": FREE,
        "rescued": FREE,
        "pushed": str,
        "unpushed": str,
        "previous_full_sha": str,
        "full_sha": str,
        "restore": [ACTION],
        "undo": [str],
    },
    "merge.outcome": {
        "number": NAT,
        "head": str,
        "status": str,
        "pr": FREE,
        "sha": Nullable(str),
        "uuid": Nullable(str),
        "message": str,
        "merged": [{"number": NAT, "sha": Nullable(str)}],
        "remaining": [
            {
                "number": NAT,
                "headSha": str,
                "baseRef": str,
                "mergeStateStatus": str,
                "checksState": str,
            }
        ],
        "branches": [
            {
                "number": NAT,
                "branch": str,
                "result": Enum("moved", "checked-out", "kept", "not-rewritten"),
                "from": str,
                "to": str,
                "path": str,
            }
        ],
        "warnings": [str],
    },
}

# A pair that is written together or not at all.
TOGETHER = (("out", "outDropped"), ("err", "errDropped"), ("error", "errorDropped"))


def _fits(value: Any, spec: Any, at: str) -> None:
    if isinstance(spec, Opt):
        spec = spec.spec
    if isinstance(spec, Nullable):
        if value is not None:
            _fits(value, spec.spec, at)
    elif isinstance(spec, Enum):
        assert value in spec.values, f"{at}: {value!r} is not one of {spec.values}"
        assert type(value) in {type(v) for v in spec.values}, f"{at}: {value!r}"
    elif isinstance(spec, Match):
        assert isinstance(value, str), f"{at}: {value!r} is not a string"
        assert spec.pattern.fullmatch(value), f"{at}: {value!r}"
    elif isinstance(spec, Either):
        assert isinstance(value, dict), f"{at}: {value!r} is not an object"
        assert value.get(spec.key) in spec.shapes, f"{at}: {spec.key} {value!r}"
        _object(value, spec.shapes[value[spec.key]], at)
    elif spec is NAT:
        assert type(value) is int and value >= 0, f"{at}: {value!r}"
    elif spec is FREE:
        assert isinstance(value, dict), f"{at}: {value!r} is not an object"
    elif isinstance(spec, list):
        assert isinstance(value, list), f"{at}: {value!r} is not a list"
        for i, item in enumerate(value):
            _fits(item, spec[0], f"{at}[{i}]")
    elif isinstance(spec, dict):
        assert isinstance(value, dict), f"{at}: {value!r} is not an object"
        _object(value, spec, at)
    else:
        # `type() is`, so a bool is never taken for the int it subclasses.
        assert type(value) is spec, f"{at}: {value!r} is not {spec.__name__}"


def _object(value: dict, spec: dict, at: str) -> None:
    unknown = set(value) - set(spec)
    assert not unknown, f"{at}: fields nobody listed: {sorted(unknown)}"
    for name, wanted in spec.items():
        if name not in value:
            assert isinstance(wanted, Opt), f"{at}: {name} is missing"
            continue
        _fits(value[name], wanted, f"{at}.{name}")


def check(row: dict) -> dict:
    """Assert `row` is exactly one documented event, and hand it back."""
    assert row.get("event") in EVENTS, f"an event nobody listed: {row.get('event')!r}"
    _object(row, {**COMMON, **EVENTS[row["event"]]}, row["event"])
    for text, count in TOGETHER:
        assert (text in row) == (count in row), f"{row['event']}: {text} and {count}"
    return row


def names() -> set[str]:
    """Every field name any event can carry, for the reference to be held to."""
    found: set[str] = set()

    def walk(spec: Any) -> None:
        if isinstance(spec, (Opt, Nullable)):
            walk(spec.spec)
        elif isinstance(spec, Either):
            found.add(spec.key)
            for shape in spec.shapes.values():
                walk(shape)
        elif isinstance(spec, list):
            walk(spec[0])
        elif isinstance(spec, dict):
            for name, inner in spec.items():
                found.add(name)
                walk(inner)

    walk(COMMON)
    walk(EVENTS)
    return found
