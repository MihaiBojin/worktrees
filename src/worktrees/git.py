"""Every git command this program can run.

A decorated function's spec is the command, written the way you would type it,
with `$name` where a value goes. Reading a module top to bottom gives the
complete list of git calls that can be issued, and `worktrees --explain`
prints it.

`shlex.split` runs once, at decoration time, on the literal spec. Only then is
each token scanned for placeholders. That ordering is the safety property: the
splitting is already over before any value is seen, so a branch named
`feat$(touch /tmp/PWNED)`, `a"b` or `has space` lands as exactly one argv
element. Nothing is ever a shell string.

`$` rather than `{}` because git's revision syntax is full of braces:
`^{tree}`, `^{commit}` and `@{upstream}` pass through a spec untouched.
"""

from __future__ import annotations

import functools
import inspect
import os
import shlex
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

Argv = tuple[str, ...]


class GitError(RuntimeError):
    """git exited with a code the caller did not declare acceptable."""


class Refused(Exception):
    """The guard will not issue this command. No flag reaches past it."""


@dataclass
class Run:
    """What one git call produced."""

    code: int
    out: str
    err: str

    def __bool__(self) -> bool:
        return self.code == 0

    @property
    def lines(self) -> list[str]:
        return self.out.splitlines()


@dataclass
class Options:
    """Set once by the CLI, read by every call."""

    verbose: bool = False
    # Every argv issued, in order. A test asserts on this; nothing else reads
    # it, so it costs a list append.
    log: list[Argv] = field(default_factory=list)


options = Options()


@dataclass(frozen=True)
class Command:
    """A decorated git call, for --explain."""

    name: str
    doc: str
    mutates: bool
    ok: tuple[int, ...]
    shape: Argv


_registry: list[Command] = []


def commands() -> list[Command]:
    """Every git command the program can issue, in declaration order."""
    return list(_registry)


# --------------------------------------------------------------------------
# the guard
# --------------------------------------------------------------------------

# Each of these destroys work git cannot give back. They are refused
# absolutely: there is no flag, and --yes least of all. The check runs on the
# resolved argv inside the wrapper, so a caller cannot assemble one past it.
_ABSOLUTE = "refusing to run"


def _has(argv: Sequence[str], *flags: str) -> bool:
    return any(a in flags for a in argv)


def _short(argv: Sequence[str], letter: str) -> bool:
    """A bundled short flag: -f matches, and so does -fdx."""
    return any(
        a.startswith("-") and not a.startswith("--") and letter in a[1:] for a in argv
    )


def guard(argv: Sequence[str]) -> None:
    """Raise Refused for a command that can lose work."""
    if not argv:
        raise Refused("empty git command")
    head = argv[0]
    rest = argv[1:]

    if head == "reset" and _has(argv, "--hard"):
        raise Refused(f"{_ABSOLUTE} `git reset --hard`; it discards the working tree")
    if head in ("checkout", "switch") and (
        _short(rest, "f") or _has(rest, "--force", "--discard-changes")
    ):
        raise Refused(
            f"{_ABSOLUTE} a forced `git {head}`; it discards the working tree"
        )
    if head == "clean" and (_short(rest, "f") or _has(rest, "--force")):
        raise Refused(f"{_ABSOLUTE} `git clean -f`; nothing restores what it deletes")
    if head == "push" and (_short(rest, "f") or _has(rest, "--force")):
        raise Refused(
            f"{_ABSOLUTE} a bare `git push --force`; use --force-with-lease instead"
        )
    if (
        head == "worktree"
        and rest
        and rest[0] == "remove"
        and (_short(rest, "f") or _has(rest, "--force"))
    ):
        raise Refused(
            f"{_ABSOLUTE} `git worktree remove --force`; it takes uncommitted work "
            "and ignored files without a word"
        )
    if head == "branch" and (
        _has(rest, "-D") or (_has(rest, "--delete") and _has(rest, "--force"))
    ):
        raise Refused(
            f"{_ABSOLUTE} `git branch -D`; a branch is deleted on proof of merge "
            "or not at all"
        )
    if head == "update-ref" and _has(rest, "-d") and len(rest) != 3:
        raise Refused(
            f"{_ABSOLUTE} `git update-ref -d` without the sha the ref must still "
            "hold; a delete that names no old value is `branch -D` spelled longer"
        )


# --------------------------------------------------------------------------
# the decorator
# --------------------------------------------------------------------------


class GitCall(Protocol):
    """What a decorated function becomes: its own arguments, plus `repo`."""

    __name__: str

    def __call__(
        self, *args: Any, repo: str | os.PathLike[str] | None = None, **kwargs: Any
    ) -> Run: ...


def _placeholders(token: str) -> list[str]:
    """The parameter names a spec token refers to."""
    names, i = [], 0
    while i < len(token):
        if token[i] != "$":
            i += 1
            continue
        if token[i + 1 : i + 2] == "$":
            i += 2
            continue
        j = i + 2 if token[i + 1 : i + 2] == "*" else i + 1
        k = j
        while k < len(token) and (token[k].isalnum() or token[k] == "_"):
            k += 1
        if k > j:
            names.append(token[j:k])
        i = max(k, i + 1)
    return names


def _expand(token: str, bound: Mapping[str, Any]) -> list[str]:
    """One spec token becomes one argv element, or several for a `$*splat`.

    `$$` is a literal `$`, and so is a `$` with no name after it, so a
    `--format=` string can hold one without meaning a parameter.
    """
    if "$*" in token:
        return [str(v) for v in bound[token[token.index("$*") + 2 :]]]
    out, i = "", 0
    while i < len(token):
        if token[i] != "$":
            out += token[i]
            i += 1
            continue
        if token[i + 1 : i + 2] == "$":
            out += "$"
            i += 2
            continue
        j = i + 1
        while j < len(token) and (token[j].isalnum() or token[j] == "_"):
            j += 1
        if j == i + 1:
            out += "$"
            i += 1
            continue
        out += str(bound[token[i + 1 : j]])
        i = j
    return [out]


def git(
    spec: str,
    *,
    ok: Iterable[int] = (0,),
    mutates: bool = False,
    env: Mapping[str, str] | None = None,
) -> Callable[[Callable[..., Any]], GitCall]:
    """Turn a spec into a function that runs it.

    spec     the command as you would type it, with `$name` where a value goes
    ok       exit codes that mean an answer rather than a failure
    mutates  takes the repository's shared refs, so it runs serially
    env      pinned environment, for a command whose output must be reproducible

    A value goes in three ways. `$name` anywhere, including inside a token, so
    `--format=$fmt` stays one element. `$*name` splats a list at that position.
    Anything the body returns is appended as a tail, for arguments with no
    fixed place.
    """
    if not isinstance(spec, str):
        raise TypeError(
            '@git takes the command as a string: @git("worktree list -z"). '
            f"Got {type(spec).__name__}."
        )
    accept = tuple(ok)
    tokens = shlex.split(spec)

    def decorate(fn: Callable[..., Any]) -> GitCall:
        signature = inspect.signature(fn)
        # At import, not at the call. A spec naming a parameter the function
        # does not have is a typo, and this is the moment it is cheapest to
        # hear about.
        for token in tokens:
            for name in _placeholders(token):
                if name not in signature.parameters:
                    raise NameError(
                        f"{fn.__name__}: spec names ${name}, which is not a "
                        f"parameter of {fn.__name__}{signature}"
                    )

        @functools.wraps(fn)
        def call(
            *args: Any, repo: str | os.PathLike[str] | None = None, **kwargs: Any
        ) -> Run:
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            argv: list[str] = []
            for token in tokens:
                argv += _expand(token, bound.arguments)
            tail = fn(*args, **kwargs)
            if tail:
                argv += [str(t) for t in tail]
            guard(argv)

            full = ["git"]
            if repo is not None:
                full += ["-C", os.fspath(repo)]
            full += argv

            options.log.append(tuple(full))
            if options.verbose:
                # shlex.join, so the printed line pastes back into a shell.
                print("+ " + shlex.join(full), file=sys.stderr)
            environ = None
            if env is not None:
                environ = {**os.environ, **env}
            proc = subprocess.run(
                full, capture_output=True, text=True, env=environ, check=False
            )
            if proc.returncode not in accept:
                raise GitError(
                    f"git {shlex.join(argv)} exited {proc.returncode}: "
                    f"{proc.stderr.strip() or '(no output)'}"
                )
            return Run(proc.returncode, proc.stdout, proc.stderr)

        _registry.append(
            Command(
                name=fn.__name__,
                doc=(fn.__doc__ or "").strip().splitlines()[0] if fn.__doc__ else "",
                mutates=mutates,
                ok=accept,
                shape=tuple(tokens),
            )
        )
        return call

    return decorate
