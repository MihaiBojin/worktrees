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
from typing import Any, Protocol

Argv = tuple[str, ...]


class GitError(RuntimeError):
    """git exited with a code the caller did not declare acceptable."""


class Refused(Exception):
    """The guard will not issue this command. No flag reaches past it."""


class Run:
    """What one git call produced."""

    __slots__ = ("code", "err", "out")

    code: int
    out: str
    err: str

    def __init__(self, code: int, out: str, err: str) -> None:
        self.code = code
        self.out = out
        self.err = err

    def __bool__(self) -> bool:
        return self.code == 0

    @property
    def lines(self) -> list[str]:
        return self.out.splitlines()


class Options:
    """Set once by the CLI, read by every call."""

    __slots__ = ("log", "verbose")

    verbose: bool
    # Every argv issued, in order. A test asserts on this; nothing else reads
    # it, so it costs a list append.
    log: list[Argv]

    def __init__(self, verbose: bool = False, log: list[Argv] | None = None) -> None:
        self.verbose = verbose
        self.log = [] if log is None else log


options = Options()


class Command:
    """A decorated git call, for --explain."""

    __slots__ = ("doc", "mutates", "name", "ok", "shape")

    name: str
    doc: str
    mutates: bool
    ok: tuple[int, ...]
    shape: Argv

    def __init__(
        self,
        name: str,
        doc: str,
        mutates: bool,
        ok: tuple[int, ...],
        shape: Argv,
    ) -> None:
        self.name = name
        self.doc = doc
        self.mutates = mutates
        self.ok = ok
        self.shape = shape


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


def _full_sha(value: str) -> bool:
    """A complete object name, 40 hex digits or 64 in a SHA-256 repository.

    An abbreviation is not one, and neither is `HEAD`, `main` or the empty
    string. git reads the empty string here as "no old value" and deletes
    whatever the ref points at, so this is the whole of the check.
    """
    return len(value) in (40, 64) and all(c in "0123456789abcdef" for c in value)


class Rule:
    """One command refused absolutely, under the name `--explain` prints.

    The list is the whole of the guard, so the refusal, the help and any test
    that walks it read the same rows. A rule written in one place and
    described in another is a rule that goes quiet.
    """

    __slots__ = ("named", "phrase", "test", "why")

    named: str  # what --explain prints
    why: str  # the clause after the semicolon
    test: Callable[[str, Sequence[str]], bool]
    # How the refusal names it, when `git <named>` is not how you would say
    # it. `{head}` is the subcommand that was about to run.
    phrase: str

    def __init__(
        self,
        named: str,
        why: str,
        test: Callable[[str, Sequence[str]], bool],
        phrase: str = "",
    ) -> None:
        self.named = named
        self.why = why
        self.test = test
        self.phrase = phrase

    def refusal(self, head: str) -> str:
        spelled = (self.phrase or f"`git {self.named}`").format(head=head)
        return f"{_ABSOLUTE} {spelled}; {self.why}"


RULES: tuple[Rule, ...] = (
    Rule(
        "reset --hard",
        "it discards the working tree",
        lambda head, rest: head == "reset" and _has(rest, "--hard"),
    ),
    Rule(
        "a forced checkout or switch",
        "it discards the working tree",
        lambda head, rest: (
            head in ("checkout", "switch")
            and (_short(rest, "f") or _has(rest, "--force", "--discard-changes"))
        ),
        phrase="a forced `git {head}`",
    ),
    Rule(
        "clean -f",
        "nothing restores what it deletes",
        lambda head, rest: (
            head == "clean" and (_short(rest, "f") or _has(rest, "--force"))
        ),
    ),
    Rule(
        "push --force",
        "use --force-with-lease instead",
        lambda head, rest: (
            head == "push" and (_short(rest, "f") or _has(rest, "--force"))
        ),
        phrase="a bare `git push --force`",
    ),
    Rule(
        "worktree remove --force",
        "it takes uncommitted work and ignored files without a word",
        lambda head, rest: (
            head == "worktree"
            and rest[:1] == ["remove"]
            and (_short(rest, "f") or _has(rest, "--force"))
        ),
    ),
    Rule(
        "branch -D",
        "a branch is deleted on proof of merge or not at all",
        lambda head, rest: (
            head == "branch"
            and (_has(rest, "-D") or (_has(rest, "--delete") and _has(rest, "--force")))
        ),
    ),
    # --stdin deletes refs with no -d anywhere in argv, so it is refused with
    # the rest: the shape below is the only delete this program may issue.
    Rule(
        "an update-ref delete that names no full sha",
        "an absent, empty or abbreviated old value, or a name like HEAD, is "
        "`git branch -D` spelled longer",
        lambda head, rest: (
            head == "update-ref"
            and _has(rest, "-d", "--stdin")
            and not (len(rest) == 3 and rest[0] == "-d" and _full_sha(rest[2]))
        ),
        phrase="`git update-ref` deleting a ref without naming the full sha it "
        "must still hold",
    ),
)


def guard(argv: Sequence[str]) -> None:
    """Raise Refused for a command that can lose work."""
    if not argv:
        raise Refused("empty git command")
    head, rest = argv[0], list(argv[1:])
    for rule in RULES:
        if rule.test(head, rest):
            raise Refused(rule.refusal(head))


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
