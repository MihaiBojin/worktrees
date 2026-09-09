"""Argument parsing and output. Data on stdout, diagnostics on stderr."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

from . import __version__, new_branch, prune
from . import repo as R
from .git import GitError, Refused, commands, options


def _err(text: str) -> None:
    print(text, file=sys.stderr)


def _pretty(path: str) -> str:
    home = str(Path.home())
    return "~" + path[len(home) :] if path.startswith(home + "/") else path


def _table(rows: list[tuple[str, ...]]) -> str:
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


def _add_prune_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--branch", default="", metavar="NAME", help="consider only that branch")
    p.add_argument("--no-fetch", action="store_true", help="assess from what is already here")
    p.add_argument(
        "--delete-ignored",
        action="store_true",
        help="let a worktree holding gitignored files go; nothing restores them",
    )
    p.add_argument("-y", "--yes", action="store_true", help="remove what it proposes")
    p.add_argument("--json", action="store_true", help="verdicts as data")
    p.add_argument("-q", "--quiet", action="store_true", help="verdicts only")
    p.add_argument("-v", "--verbose", action="store_true", help="print every git command")
    p.add_argument("--explain", action="store_true", help="print every git command and exit")


def run_prune(args: argparse.Namespace) -> int:
    options.verbose = args.verbose
    if args.explain:
        return _explain()

    remote = R.remote()
    if not args.no_fetch:
        if remote:
            R.fetch(remote)
    elif not args.quiet:
        _err("assessing from what is already here; refs may be stale (--no-fetch)")

    head, warning = R.head_ref(remote, online=not args.no_fetch)
    if warning and not args.quiet:
        _err(warning)
    if not head:
        _err("cannot tell which branch this repository branches from")
        return 2
    head_branch = R.ref_name(head)
    if remote:
        head_branch = head_branch.removeprefix(remote + "/")

    # git's own bookkeeping for worktrees whose directories somebody removed
    # by hand. A stale entry is a record answering for a directory that is not
    # there.
    prune.prune_records()

    verdicts = prune.assess(args.branch, head, head_branch, args.delete_ignored)

    if args.json:
        print(
            json.dumps(
                {
                    "head": head,
                    "verdicts": [
                        {
                            "verdict": v.verdict,
                            "branch": v.branch,
                            "path": v.path,
                            "why": v.why,
                        }
                        for v in verdicts
                    ],
                },
                indent=2,
            )
        )
    elif not verdicts:
        print("nothing to prune")
        return 0
    else:
        rows: list[tuple[str, ...]] = [("VERDICT", "BRANCH", "WHY", "PATH")]
        rows += [(v.verdict, v.label, v.why, _pretty(v.path)) for v in verdicts]
        print(_table(rows))

    go = [v for v in verdicts if v.verdict == prune.GO]
    unknown = [v for v in verdicts if v.verdict == prune.UNKNOWN]
    kept = len(verdicts) - len(go) - len(unknown)

    if not args.json and not args.quiet:
        print()
        print(f"{len(go)} to remove, {kept} kept, {len(unknown)} unclear")

    # A run that cannot tell names which, whatever happens next. --yes answers
    # "remove the ones proved finished", not "assume the rest are", and an
    # unclear verdict presented as a kept one is how somebody acts on the
    # wrong one.
    if unknown and not args.json:
        names = ", ".join(v.label for v in unknown)
        _err(f"unclear, and --yes does not touch these: {names}")

    if not go:
        return 0
    if not args.yes:
        if not args.json:
            _err(f"nothing removed; pass --yes to remove the {len(go)} above")
        return 0

    print()
    failed = prune.sweep(go, args.delete_ignored, print)
    if failed:
        _err(f"{failed} of {len(go)} could not be removed; each said why above")
        return 1
    print(f"removed {len(go)} worktree(s)")
    return 0


def _add_create_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("name", nargs="?", default="", metavar="NAME", help="the branch to start")
    p.add_argument("--no-fetch", action="store_true", help="branch off what is already here")
    p.add_argument("--json", action="store_true", help="the result as data")
    p.add_argument("-q", "--quiet", action="store_true", help="say nothing on success")
    p.add_argument("-v", "--verbose", action="store_true", help="print every git command")
    p.add_argument("--explain", action="store_true", help="print every git command and exit")


def run_create(args: argparse.Namespace) -> int:
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


def _parser(prog: str, description: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=prog, description=description)
    p.add_argument("--version", action="version", version=__version__)
    return p


def gwp(argv: list[str] | None = None) -> int:
    """The alias a person types. It never cds, so it needs no shell."""
    p = _parser("gwp", "Say which worktrees are finished, and why. Removes nothing unless --yes.")
    _add_prune_flags(p)
    return _dispatch(p, argv, run_prune)


def gwnb(argv: list[str] | None = None) -> int:
    """The same, for starting a branch. Checking out needs no shell either."""
    p = _parser("gwnb", "Fetch, then branch NAME off the head branch and check it out.")
    _add_create_flags(p)
    return _dispatch(p, argv, run_create)


# The subcommand each name runs, and the flags it takes.
_COMMANDS = {
    "prune": (run_prune, _add_prune_flags, "say which worktrees are finished"),
    "new-branch": (run_create, _add_create_flags, "start a branch off the head branch"),
}


def main(argv: list[str] | None = None) -> int:
    """The CLI a shim and an agent call."""
    p = _parser("worktrees", "Git worktree commands that refuse to lose work.")
    subs = p.add_subparsers(dest="command")
    for name, (_, flags, help_text) in _COMMANDS.items():
        flags(subs.add_parser(name, help=help_text))
    args_in = sys.argv[1:] if argv is None else argv
    if args_in and args_in[0].startswith("-") and args_in[0] not in (
        "-h",
        "--help",
        "--version",
    ):
        # `worktrees --explain` is not about one command; prune answers it.
        args_in = ["prune", *args_in]
    return _dispatch(p, args_in, None)


def _dispatch(
    p: argparse.ArgumentParser,
    argv: list[str] | None,
    run: Callable[[argparse.Namespace], int] | None,
) -> int:
    raw = sys.argv[1:] if argv is None else argv
    # Before parsing, so it is refused rather than absorbed. A flag meaning
    # "do not act" on a command that does not act is a no-op wearing the
    # clothes of a safety feature, and somebody will one day read it as the
    # reason a sweep was safe.
    if "--dry-run" in raw:
        _err(
            "there is no --dry-run: this assesses and prints, and only --yes "
            "removes anything"
        )
        return 2
    args = p.parse_args(raw)
    if run is None:
        if args.command is None:
            p.print_help()
            return 0
        run = _COMMANDS[args.command][0]
    try:
        return run(args)
    except Refused as exc:
        _err(str(exc))
        return 3
    except GitError as exc:
        _err(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
