"""Every git command this program can run.

A decorated function's body is the command. It returns the argv that follows
`git`, so reading a module top to bottom gives the complete list of git calls
that can be issued, and `worktrees --explain` prints it.

The argv is a real list. Nothing is ever a shell string, so a branch named
`; rm -rf ~` is an argument and not a command.
"""

from __future__ import annotations

import functools
import inspect
import os
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

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


# --------------------------------------------------------------------------
# the decorator
# --------------------------------------------------------------------------


def _placeholder_shape(func: Callable[..., Argv]) -> Argv:
    """The argv with `<param>` standing in for each argument.

    Sound only because a decorated body does nothing but return a tuple built
    from its parameters. That is the same constraint that makes the module
    readable as a list of commands.
    """
    params = inspect.signature(func).parameters
    try:
        return tuple(func(*(f"<{p}>" for p in params)))
    except Exception:  # a body that needs a real value gets named, not guessed
        return (f"<{func.__name__}>",)


def git(
    func: Callable[..., Argv] | None = None,
    *,
    ok: Iterable[int] = (0,),
    mutates: bool = False,
    opts: Argv = (),
    env: Mapping[str, str] | None = None,
) -> Callable[..., Run] | Callable[[Callable[..., Argv]], Callable[..., Run]]:
    """Turn a function that names a git command into one that runs it.

    ok       exit codes that mean an answer rather than a failure
    mutates  takes the repository's shared refs, so it runs serially
    opts     git's own options, which go before the subcommand
    env      pinned environment, for a command whose output must be reproducible
    """
    accept = tuple(ok)

    def decorate(fn: Callable[..., Argv]) -> Callable[..., Run]:
        @functools.wraps(fn)
        def call(*args: str, repo: str | os.PathLike[str] | None = None) -> Run:
            argv = tuple(fn(*args))
            guard(argv)

            full = ["git"]
            if repo is not None:
                full += ["-C", os.fspath(repo)]
            full += [*opts, *argv]

            options.log.append(tuple(full))
            if options.verbose:
                print("+ " + " ".join(full), file=sys.stderr)
            environ = None
            if env is not None:
                environ = {**os.environ, **env}
            proc = subprocess.run(
                full, capture_output=True, text=True, env=environ, check=False
            )
            if proc.returncode not in accept:
                raise GitError(
                    f"git {' '.join(argv)} exited {proc.returncode}: "
                    f"{proc.stderr.strip() or '(no output)'}"
                )
            return Run(proc.returncode, proc.stdout, proc.stderr)

        _registry.append(
            Command(
                name=fn.__name__,
                doc=(fn.__doc__ or "").strip().splitlines()[0] if fn.__doc__ else "",
                mutates=mutates,
                ok=accept,
                shape=(*opts, *_placeholder_shape(fn)),
            )
        )
        return call

    if func is not None:
        return decorate(func)
    return decorate
