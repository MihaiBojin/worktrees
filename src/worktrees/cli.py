"""Argument parsing and output. Data on stdout, diagnostics on stderr."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from . import __version__, new_branch, prune, verdicts
from . import repo as R
from .git import GitError, Refused, commands, options


def _err(text: str) -> None:
    print(text, file=sys.stderr)


def _pretty(path: str) -> str:
    home = str(Path.home())
    return "~" + path[len(home) :] if path.startswith(home + "/") else path


def _table(rows: Sequence[tuple[str, ...]]) -> str:
    """Columns wide enough for their content, the last one unpadded."""
    if not rows:
        return ""
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    out = []
    for row in rows:
        cells = [c.ljust(widths[i]) for i, c in enumerate(row[:-1])]
        out.append("  ".join([*cells, row[-1]]).rstrip())
    return "\n".join(out)


def _explain() -> int:
    """Every git command the program can issue."""
    rows = [("", "COMMAND", "GIT")]
    for c in commands():
        rows.append(("!" if c.mutates else " ", c.name, "git " + " ".join(c.shape)))
    print(_table(rows))
    print()
    print(
        "! takes the repository's shared refs and runs serially. The guard "
        "refuses\n  reset --hard, a forced checkout or switch, clean -f, push "
        "--force,\n  worktree remove --force and branch -D outright, whatever "
        "flags are passed."
    )
    return 0


# --------------------------------------------------------------------------
# the assessment both commands share
# --------------------------------------------------------------------------


def _add_assess_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--branch", default="", metavar="NAME", help="consider only that branch"
    )
    p.add_argument("--no-fetch", action="store_true", help="use the refs already here")
    p.add_argument(
        "--delete-ignored",
        action="store_true",
        help="count a worktree holding gitignored files as removable; nothing "
        "restores them",
    )
    p.add_argument("--json", action="store_true", help="verdicts as data")
    p.add_argument("-q", "--quiet", action="store_true", help="verdicts only")
    p.add_argument(
        "-v", "--verbose", action="store_true", help="print every git command"
    )
    p.add_argument(
        "--explain", action="store_true", help="print every git command and exit"
    )


class _Stop(Exception):
    """A refusal with an exit code, raised where the reason is known."""

    def __init__(self, message: str, code: int) -> None:
        super().__init__(message)
        self.code = code


def _assess(
    args: argparse.Namespace,
) -> tuple[list[verdicts.Verdict], list[R.Worktree], str]:
    """Fetch, resolve the head branch, and judge every worktree.

    The one path both commands take, so `prune` can only ever act on what
    `status` printed.
    """
    remote = R.remote()
    if not args.no_fetch:
        if remote:
            R.fetch(remote)
    elif not args.quiet:
        _err("using the refs already here; they may be stale (--no-fetch)")

    head, warning = R.head_ref(remote, online=not args.no_fetch)
    if warning and not args.quiet:
        _err(warning)
    if not head:
        raise _Stop("cannot tell which branch this repository branches from", 2)
    head_branch = R.ref_name(head)
    if remote:
        head_branch = head_branch.removeprefix(remote + "/")

    records = R.worktrees()
    return (
        verdicts.assess(records, args.branch, head, head_branch, args.delete_ignored),
        verdicts.stale(records),
        head,
    )


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------


def run_status(args: argparse.Namespace) -> int:
    """Say what is here. Nothing in this path removes anything."""
    options.verbose = args.verbose
    if args.explain:
        return _explain()

    rows, stale, head = _assess(args)
    go = prune.removable(rows)
    unknown = [v for v in rows if v.verdict == verdicts.UNKNOWN]

    if args.json:
        print(
            json.dumps(
                {
                    "head": head,
                    "stale": [w.path for w in stale],
                    "verdicts": [
                        {
                            "verdict": v.verdict,
                            "branch": v.branch,
                            "path": v.path,
                            "why": v.why,
                        }
                        for v in rows
                    ],
                },
                indent=2,
            )
        )
        return 0

    if not rows:
        print("no worktrees besides the main checkout")
    else:
        table: list[tuple[str, ...]] = [("VERDICT", "BRANCH", "WHY", "PATH")]
        table += [(v.verdict, v.label, v.why, _pretty(v.path)) for v in rows]
        print(_table(table))

    if args.quiet:
        return 0

    print()
    kept = len(rows) - len(go) - len(unknown)
    print(f"{len(go)} removable, {kept} kept, {len(unknown)} unclear")
    if stale:
        _err(
            f"{len(stale)} stale record(s) for directories that are gone; "
            "gwp clears them"
        )
    if unknown:
        names = ", ".join(v.label for v in unknown)
        _err(f"unclear, and gwp does not touch these: {names}")
    if go:
        _err(f"gwp removes the {len(go)} marked removable")
    return 0


# --------------------------------------------------------------------------
# prune
# --------------------------------------------------------------------------


def run_prune(args: argparse.Namespace) -> int:
    """Remove exactly what status marks removable, having asked first."""
    options.verbose = args.verbose
    if args.explain:
        return _explain()

    # git's own bookkeeping for worktrees whose directories somebody removed
    # by hand. A stale record answers for a directory that is not there, and
    # neither command may propose removing one.
    prune.prune_records()

    rows, _, _ = _assess(args)
    go = prune.removable(rows)
    unknown = [v for v in rows if v.verdict == verdicts.UNKNOWN]

    if unknown and not args.quiet:
        names = ", ".join(v.label for v in unknown)
        _err(f"unclear, and this does not touch them: {names}")

    if not go:
        if not args.json:
            print("nothing to remove; gws says why")
        else:
            print(json.dumps({"removed": [], "failed": 0}, indent=2))
        return 0

    # What goes, and what puts it back, before anything does. On stderr with
    # the rest of the diagnostics, so stdout carries the result alone;
    # --quiet and --yes do not silence it.
    prune.plan(go, args.delete_ignored, _err)

    if not args.yes:
        try:
            if not prune.confirm(len(go)):
                _err("nothing removed")
                return 0
        except Refused as exc:
            _err(str(exc))
            return 2

    failed = prune.sweep(go, args.delete_ignored, _err)
    removed = [v.label for v in go]
    if args.json:
        print(json.dumps({"removed": removed, "failed": failed}, indent=2))
    elif failed:
        _err(f"{failed} of {len(go)} could not be removed; each said why above")
    else:
        print(f"removed {len(go)} worktree(s)")
    return 1 if failed else 0


# --------------------------------------------------------------------------
# new-branch
# --------------------------------------------------------------------------


def _add_new_branch_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "name", nargs="?", default="", metavar="NAME", help="the branch to start"
    )
    p.add_argument(
        "--no-fetch", action="store_true", help="branch off what is already here"
    )
    p.add_argument("--json", action="store_true", help="the result as data")
    p.add_argument("-q", "--quiet", action="store_true", help="say nothing on success")
    p.add_argument(
        "-v", "--verbose", action="store_true", help="print every git command"
    )
    p.add_argument(
        "--explain", action="store_true", help="print every git command and exit"
    )


def run_new_branch(args: argparse.Namespace) -> int:
    options.verbose = args.verbose
    if args.explain:
        return _explain()
    if not args.name:
        _err("usage: gwnb NAME")
        return 2

    warn = None if args.quiet else _err
    if args.no_fetch and not args.quiet:
        _err("branching from what is already here; refs may be stale (--no-fetch)")

    try:
        started = new_branch.create(args.name, fetch=not args.no_fetch, warn=warn)
    except new_branch.Refusal as exc:
        _err(str(exc))
        return 1

    if args.json:
        print(
            json.dumps(
                {"branch": started.branch, "base": started.base, "sha": started.sha},
                indent=2,
            )
        )
    elif not args.quiet:
        print(f"{started.branch} from {R.ref_name(started.base)} at {started.sha}")
    return 0


# --------------------------------------------------------------------------
# entry points
# --------------------------------------------------------------------------


def _add_prune_flags(p: argparse.ArgumentParser) -> None:
    _add_assess_flags(p)
    p.add_argument(
        "-y", "--yes", action="store_true", help="do not ask before removing"
    )


_COMMANDS = {
    "status": (run_status, _add_assess_flags, "say which worktrees are finished"),
    "prune": (run_prune, _add_prune_flags, "remove the ones status marks removable"),
    "new-branch": (
        run_new_branch,
        _add_new_branch_flags,
        "start a branch off the head branch",
    ),
}

_STATUS_HELP = "Say which of this repository's worktrees are finished, and why."
_PRUNE_HELP = "Remove the worktrees gws marks removable, and their branches."
_NEW_BRANCH_HELP = "Fetch, then branch NAME off the head branch and check it out."


def _parser(prog: str, description: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=prog, description=description)
    p.add_argument("--version", action="version", version=__version__)
    return p


def gws(argv: list[str] | None = None) -> int:
    """Read-only. There is no flag here that removes anything."""
    p = _parser("gws", _STATUS_HELP)
    _add_assess_flags(p)
    return _dispatch(p, argv, run_status)


def gwp(argv: list[str] | None = None) -> int:
    """Asks before it acts, unless --yes."""
    p = _parser("gwp", _PRUNE_HELP)
    _add_prune_flags(p)
    return _dispatch(p, argv, run_prune)


def gwnb(argv: list[str] | None = None) -> int:
    """Checking out needs no shell either."""
    p = _parser("gwnb", _NEW_BRANCH_HELP)
    _add_new_branch_flags(p)
    return _dispatch(p, argv, run_new_branch)


def main(argv: list[str] | None = None) -> int:
    """The CLI a shim and an agent call."""
    p = _parser("worktrees", "Git worktree commands that refuse to lose work.")
    subs = p.add_subparsers(dest="command")
    for name, (_, flags, help_text) in _COMMANDS.items():
        flags(subs.add_parser(name, help=help_text))
    args_in = sys.argv[1:] if argv is None else argv
    if (
        args_in
        and args_in[0].startswith("-")
        and args_in[0]
        not in (
            "-h",
            "--help",
            "--version",
        )
    ):
        # `worktrees --explain` is not about one command; status answers it.
        args_in = ["status", *args_in]
    return _dispatch(p, args_in, None)


def _dispatch(
    p: argparse.ArgumentParser,
    argv: list[str] | None,
    run: Callable[[argparse.Namespace], int] | None,
) -> int:
    raw = sys.argv[1:] if argv is None else argv
    # Before parsing, so it is refused rather than absorbed. A flag meaning
    # "do not act" on a command that already asks is a no-op wearing the
    # clothes of a safety feature, and somebody will one day read it as the
    # reason a sweep was safe.
    if "--dry-run" in raw:
        _err("there is no --dry-run: gws reports, and gwp asks before removing")
        return 2
    args = p.parse_args(raw)
    if run is None:
        if args.command is None:
            p.print_help()
            return 0
        run = _COMMANDS[args.command][0]
    try:
        return run(args)
    except _Stop as exc:
        _err(str(exc))
        return exc.code
    except Refused as exc:
        _err(str(exc))
        return 3
    except GitError as exc:
        _err(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
