"""Argument parsing and output. Data on stdout, diagnostics on stderr."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence

from . import __version__, new_branch, pick, prune, verdicts
from . import repo as R
from . import rotate as rotate_mod
from . import worktree as wt_mod
from .git import GitError, Refused, commands, options


def _err(text: str) -> None:
    print(text, file=sys.stderr)


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
        "--no-forge",
        action="store_true",
        help="decide from git alone; never ask the forge",
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
    judge: Callable[..., list[verdicts.Verdict]] | None = None,
) -> tuple[list[verdicts.Verdict], list[R.Worktree], str]:
    """Fetch, resolve the head branch, and judge every worktree.

    The one path every command takes, so `prune` can only ever act on what
    `status` printed. `judge` is how `remove` asks for the one you stand in
    to be judged like any other, having stepped out of it first.
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

    only = getattr(args, "branch", "")
    ignored = getattr(args, "delete_ignored", False)
    records = R.worktrees()
    # The forge is asked last and only where git could not tell. It costs a
    # round trip, and a branch merged as part of a stack is the one case
    # content cannot answer: its changes reach the head branch across several
    # squashes, so a stale intermediate and real work look alike to a diff.
    ask_forge = not getattr(args, "no_forge", False)
    rows = (
        judge(only, head, head_branch, ignored, ask_forge=ask_forge)
        if judge
        else verdicts.assess(
            records, only, head, head_branch, ignored, ask_forge=ask_forge
        )
    )
    return rows, verdicts.stale(records), head


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
        table += [(v.verdict, v.label, v.why, v.path) for v in rows]
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
    prune.worktree_prune()

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
        # The program the caller typed, so `worktrees new-branch` does not
        # answer with the name of its own alias.
        _err(f"usage: {args.prog} NAME")
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
# rotate
# --------------------------------------------------------------------------


def _add_rotate_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--no-fetch", action="store_true", help="work from what is already here"
    )
    p.add_argument("--json", action="store_true", help="the result as data")
    p.add_argument("-q", "--quiet", action="store_true", help="say nothing on success")
    p.add_argument(
        "-v", "--verbose", action="store_true", help="print every git command"
    )
    p.add_argument(
        "--explain", action="store_true", help="print every git command and exit"
    )


def run_rotate(args: argparse.Namespace) -> int:
    options.verbose = args.verbose
    if args.explain:
        return _explain()

    warn = None if args.quiet else _err
    if args.no_fetch and not args.quiet:
        _err("working from what is already here; refs may be stale (--no-fetch)")

    try:
        result = rotate_mod.rotate(fetch=not args.no_fetch, warn=warn)
    except new_branch.Refusal as exc:
        _err(str(exc))
        return 1

    if isinstance(result, rotate_mod.CaughtUp):
        if args.json:
            print(json.dumps({"branch": result.branch, "at": result.at}, indent=2))
        elif not args.quiet:
            print(f"{result.branch} is at {result.at}")
        return 0

    if args.json:
        print(
            json.dumps(
                {
                    "branch": result.branch,
                    "stem": result.stem,
                    "base": result.base,
                    "sha": result.sha,
                },
                indent=2,
            )
        )
    elif not args.quiet:
        print(f"{result.branch} from {R.ref_name(result.base)} at {result.sha}")
    return 0


# --------------------------------------------------------------------------
# the four that land you somewhere
# --------------------------------------------------------------------------


def _add_cd_flags(p: argparse.ArgumentParser) -> None:
    """Shared by every command whose answer is a directory."""
    p.add_argument("--json", action="store_true", help="the result as data")
    p.add_argument("-q", "--quiet", action="store_true", help="the path alone")
    p.add_argument(
        "-v", "--verbose", action="store_true", help="print every git command"
    )
    p.add_argument(
        "--explain", action="store_true", help="print every git command and exit"
    )


def _add_add_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("name", nargs="?", default="", metavar="NAME", help="the branch")
    p.add_argument(
        "base", nargs="?", default="", metavar="BASE", help="what to branch from"
    )
    p.add_argument(
        "--no-fetch", action="store_true", help="branch from what is already here"
    )
    _add_cd_flags(p)


def _add_list_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "query", nargs="?", default="", metavar="QUERY", help="narrow the list"
    )
    p.add_argument(
        "-l", "--list", action="store_true", help="print them all and pick none"
    )
    _add_cd_flags(p)


def _add_move_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "name", nargs="?", default="", metavar="NEW", help="the new branch name"
    )
    _add_cd_flags(p)


def _add_remove_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "query", nargs="?", default="", metavar="PATH|QUERY", help="which one"
    )
    p.add_argument(
        "-f", "--force", action="store_true", help="remove it even when unfinished"
    )
    p.add_argument(
        "--delete-ignored",
        action="store_true",
        help="also delete its gitignored files; nothing restores them",
    )
    p.add_argument("--no-fetch", action="store_true", help="use the refs already here")
    p.add_argument("-y", "--yes", action="store_true", help="do not ask")
    _add_cd_flags(p)


def _landed(args: argparse.Namespace, landed: wt_mod.Landed) -> int:
    """One destination on stdout, so `cd $(gwa x)` works."""
    if args.json:
        print(
            json.dumps(
                {
                    "path": landed.path,
                    "branch": landed.branch,
                    "created": landed.created,
                },
                indent=2,
            )
        )
        return 0
    print(landed.path)
    return 0


def run_add(args: argparse.Namespace) -> int:
    options.verbose = args.verbose
    if args.explain:
        return _explain()
    if not args.name:
        _err(f"usage: {args.prog} NAME [BASE]")
        return 2

    warn = None if args.quiet else _err
    try:
        landed = wt_mod.add(args.name, args.base, fetch=not args.no_fetch, warn=warn)
    except new_branch.Refusal as exc:
        _err(str(exc))
        return 1
    if not args.quiet and not args.json:
        made = "on a new branch" if landed.created else "on the branch already here"
        _err(f"{landed.branch} {made}")
    return _landed(args, landed)


def run_list(args: argparse.Namespace) -> int:
    options.verbose = args.verbose
    if args.explain:
        return _explain()

    main = R.main_worktree()
    rows = [w for w in R.worktrees() if "bare" not in w.flags and w.path != main]
    found = pick.matches(args.query, rows)

    if args.json:
        print(
            json.dumps(
                [
                    {"branch": w.branch, "path": w.path, "flags": sorted(w.flags)}
                    for w in found
                ],
                indent=2,
            )
        )
        return 0

    if not found:
        _err(
            "no worktree matches"
            if args.query
            else "no worktrees besides the main checkout"
        )
        return 1

    if args.list:
        width = max(len(w.label) for w in found)
        for w in found:
            print(f"{w.label:<{width}}  {w.path}")
        return 0

    chosen = pick.choose(found, _err)
    if chosen is None:
        _err("nothing picked")
        return 0
    print(chosen.path)
    return 0


def run_move(args: argparse.Namespace) -> int:
    options.verbose = args.verbose
    if args.explain:
        return _explain()
    if not args.name:
        _err(f"usage: {args.prog} NEW")
        return 2

    try:
        landed = wt_mod.move(args.name)
    except new_branch.Refusal as exc:
        _err(str(exc))
        return 1
    if not args.quiet and not args.json:
        _err(f"renamed to {landed.branch}")
    return _landed(args, landed)


def run_remove(args: argparse.Namespace) -> int:
    options.verbose = args.verbose
    if args.explain:
        return _explain()

    rows, _, _ = _assess(args, judge=wt_mod.removable)
    found = pick.matches(
        args.query, [R.Worktree(v.path, "", v.branch, frozenset()) for v in rows]
    )
    by_path = {v.path: v for v in rows}
    candidates = [by_path[w.path] for w in found if w.path in by_path]

    if not candidates:
        _err(
            "no worktree matches"
            if args.query
            else "no worktrees besides the main checkout"
        )
        return 1

    picked = pick.choose(
        [R.Worktree(v.path, "", v.branch, frozenset()) for v in candidates], _err
    )
    if picked is None:
        _err("nothing picked")
        return 0
    chosen = by_path[picked.path]

    if chosen.verdict != prune.REMOVE and not args.force:
        _err(f"{chosen.label} is not finished: {chosen.why}")
        _err("pass --force to remove the worktree anyway; the branch is kept")
        return 1

    prune.plan([chosen], args.delete_ignored, _err)
    if not args.yes:
        try:
            if not prune.confirm(1):
                _err("nothing removed")
                return 0
        except Refused as exc:
            _err(str(exc))
            return 2

    keep_branch = chosen.verdict != prune.REMOVE
    # --force keeps the branch: the worktree was in the way, the work was not.
    destination = wt_mod.remove(
        prune.Verdict(chosen.verdict, "", chosen.path, chosen.why)
        if keep_branch
        else chosen,
        _err,
    )
    if not args.quiet and not args.json:
        _err(f"removed {chosen.label}")
    if args.json:
        print(json.dumps({"removed": chosen.label, "path": destination}, indent=2))
        return 0
    if destination:
        print(destination)
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
    "rotate": (run_rotate, _add_rotate_flags, "start the next branch after this one"),
    "add": (run_add, _add_add_flags, "create a worktree and land you in it"),
    "list": (run_list, _add_list_flags, "pick one of this repository's worktrees"),
    "move": (run_move, _add_move_flags, "rename this worktree's branch and move it"),
    "remove": (
        run_remove,
        _add_remove_flags,
        "remove a worktree whose branch is finished",
    ),
}

_STATUS_HELP = "Say which of this repository's worktrees are finished, and why."
_PRUNE_HELP = "Remove the worktrees gws marks removable, and their branches."
_NEW_BRANCH_HELP = "Fetch, then branch NAME off the head branch and check it out."
_ROTATE_HELP = (
    "Start the next branch after this one, named <stem>-YYYY-MM-DD_NNN. On the "
    "head branch there is no chain to continue, so it catches that up to the "
    "remote instead."
)


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


def gwa(argv: list[str] | None = None) -> int:
    """Prints the destination. The shell function does the cd."""
    p = _parser("gwa", "Create a worktree for NAME and print where it is.")
    _add_add_flags(p)
    return _dispatch(p, argv, run_add)


def gwl(argv: list[str] | None = None) -> int:
    """The same, for one that already exists."""
    p = _parser("gwl", "Pick one of this repository's worktrees and print its path.")
    _add_list_flags(p)
    return _dispatch(p, argv, run_list)


def gwm(argv: list[str] | None = None) -> int:
    """Renaming the directory you stand in is why this one is not optional."""
    p = _parser("gwm", "Rename this worktree's branch to NEW and move it to match.")
    _add_move_flags(p)
    return _dispatch(p, argv, run_move)


def gwr(argv: list[str] | None = None) -> int:
    """Prints a path only when the caller was standing in what went."""
    p = _parser("gwr", "Remove a worktree whose branch is finished, and the branch.")
    _add_remove_flags(p)
    return _dispatch(p, argv, run_remove)


def gwrot(argv: list[str] | None = None) -> int:
    """Naming a branch and continuing a series are two commands."""
    p = _parser("gwrot", _ROTATE_HELP)
    _add_rotate_flags(p)
    return _dispatch(p, argv, run_rotate)


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
    # argparse gives a subparser its own prog; rebuild it rather than reach
    # into the private table for it.
    command = getattr(args, "command", None)
    args.prog = f"{p.prog} {command}" if command else p.prog
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
