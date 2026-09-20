"""Argument parsing and output. Data on stdout, diagnostics on stderr."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from . import __version__, render
from .errors import GitError, Refused
from .render import BLUE, BOLD, DIM, GREEN, RED, YELLOW, Cell, Row, table

if TYPE_CHECKING:  # names used in annotations, which never run
    from . import repo as R
    from . import verdicts
    from . import worktree as wt_mod


def _err(text: str) -> None:
    print(text, file=sys.stderr)


def _same(a: str, b: str) -> bool:
    """One directory under two spellings, or under a symlink."""
    return bool(a) and bool(b) and Path(a).resolve() == Path(b).resolve()


def _verdict_table(rows: list[verdicts.Verdict]) -> str:
    """The table `gws` prints, which `gwp` prints too rather than name it."""
    from . import verdicts

    # Built here rather than at module level: naming the verdicts is what
    # imports the module, and `gw version` has none to colour.
    code = {
        verdicts.REMOVE: GREEN,
        verdicts.KEEP: BLUE,
        verdicts.UNKNOWN: YELLOW,
    }
    head: Row = (
        Cell("VERDICT", DIM),
        Cell("BRANCH", DIM),
        Cell("WHY", DIM),
        Cell("PATH", DIM),
    )
    body: list[Row] = [
        (
            Cell(v.verdict, code.get(v.verdict, "")),
            Cell(v.label, BOLD),
            Cell(v.why),
            Cell(v.path, DIM),
        )
        for v in rows
    ]
    return table([head, *body])


def _counted(rows: list[verdicts.Verdict]) -> str:
    """How many of each verdict, in the verdicts' own colours."""
    from . import prune, verdicts

    go = len(prune.removable(rows))
    unclear = len([v for v in rows if v.verdict == verdicts.UNKNOWN])
    kept = len(rows) - go - unclear
    return (
        f"{render.out(str(go), GREEN)} removable, "
        f"{render.out(str(kept), BLUE)} kept, "
        f"{render.out(str(unclear), YELLOW)} unclear"
    )


def _explain() -> int:
    """Every git command the program can issue."""
    from .git import RULES, commands

    rows: list[Row] = [(Cell("", DIM), Cell("COMMAND", DIM), Cell("GIT", DIM))]
    for c in commands():
        rows.append(
            (
                Cell("!", RED) if c.mutates else Cell(" "),
                Cell(c.name, BOLD),
                Cell("git " + " ".join(c.shape)),
            )
        )
    print(table(rows))
    print()
    # Generated from the guard itself. The footer used to be a sentence
    # written by hand in this file, and it fell a rule behind the moment one
    # was added.
    import textwrap

    named = [rule.named for rule in RULES]
    listed = ", ".join(named[:-1]) + " and " + named[-1]
    print(
        textwrap.fill(
            "! takes the repository's shared refs and runs serially. The guard "
            f"refuses these outright, whatever flags are passed: {listed}.",
            width=76,
            subsequent_indent="  ",
        )
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
    ask_forge: bool | None = None,
) -> tuple[list[verdicts.Verdict], list[R.Worktree], str, str, list[R.Worktree]]:
    """Fetch, resolve the head branch, and judge every worktree.

    The one path every command takes, so `prune` can only ever act on what
    `status` printed. `judge` is how `remove` asks for the one you stand in
    to be judged like any other, having stepped out of it first.
    """
    from . import repo as R
    from . import verdicts

    remote = R.remote()
    online = not args.no_fetch
    if online:
        # An unreachable remote is not a reason to refuse to answer. It lands
        # where --no-fetch already goes, and says so, and the head-branch
        # ladder is told not to spend a second round trip on the same remote.
        if remote and not R.fetch(remote):
            online = False
            if not args.quiet:
                _err(
                    f"{remote} could not be fetched; judging from the refs already here"
                )
    elif not args.quiet:
        _err("using the refs already here; they may be stale (--no-fetch)")

    head, warning = R.head_ref(remote, online=online)
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
    if ask_forge is None:
        ask_forge = not getattr(args, "no_forge", False)
    rows = (
        judge(only, head, head_branch, ignored, ask_forge=ask_forge)
        if judge
        else verdicts.assess(
            records,
            only,
            head,
            head_branch,
            ignored,
            ask_forge=ask_forge,
            # `gws` and `gwp` report; `gwr` acts, and takes this list as its
            # candidates, so it asks through `judge` and gets it without.
            include_main=True,
        )
    )
    return rows, verdicts.stale(records), head, head_branch, records


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------


def run_status(args: argparse.Namespace) -> int:
    """Say what is here. Nothing in this path removes anything."""
    import json

    from . import layout, prune, verdicts
    from .git import options

    options.verbose = args.verbose
    if args.explain:
        return _explain()

    rows, stale, head, _, records = _assess(args)
    go = prune.removable(rows)
    unknown = [v for v in rows if v.verdict == verdicts.UNKNOWN]

    if args.json:
        print(
            json.dumps(
                {
                    "head": head,
                    "stale": [w.path for w in stale],
                    # `ignored` and `sha` are fields rather than facts to
                    # parse back out of `why`. A caller deciding whether to
                    # pass --delete-ignored wants the count, not a sentence
                    # that happens to contain it, and the sha is what the
                    # verdict was formed against.
                    "verdicts": [
                        {
                            "verdict": v.verdict,
                            "branch": v.branch,
                            "path": v.path,
                            "why": v.why,
                            "ignored": v.ignored,
                            "sha": v.sha,
                        }
                        for v in rows
                    ],
                },
                indent=2,
            )
        )
        return 0

    print(_verdict_table(rows))

    if args.quiet:
        return 0

    print()
    print(_counted(rows))
    if len(rows) < 2:
        _err("no worktrees besides the main checkout; gwa NAME makes one")
    if stale:
        _err(
            f"{len(stale)} stale record(s) for directories that are gone; "
            "gwp clears them"
        )
    # Every repository beside this one shares the same `.worktrees` root, and
    # nothing else says which of the directories there are somebody else's.
    # git lists the main checkout first, so records[0] is it.
    main = records[0].path if records else ""
    others = layout.neighbours(main, [w.path for w in records]) if main else 0
    if others:
        root = layout.worktrees_root(main)
        _err(f"{others} worktree(s) under {root} belong to another repository")
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
    import json

    from . import prune, verdicts
    from .git import options

    options.verbose = args.verbose
    if args.explain:
        return _explain()

    # git's own bookkeeping for worktrees whose directories somebody removed
    # by hand. A stale record answers for a directory that is not there, and
    # neither command may propose removing one.
    prune.worktree_prune()

    rows, _, _, _, _ = _assess(args)
    go = prune.removable(rows)
    unknown = [v for v in rows if v.verdict == verdicts.UNKNOWN]

    if not go:
        # The reason each one stayed, here, rather than the name of the
        # command that would have printed it.
        if args.json:
            print(json.dumps({"removed": [], "failed": 0}, indent=2))
        else:
            print(_verdict_table(rows))
            if not args.quiet:
                print()
                print(f"nothing to remove; {_counted(rows)}")
                if len(rows) < 2:
                    _err("no worktrees besides the main checkout; gwa NAME makes one")
        return 0

    if unknown and not args.quiet:
        names = ", ".join(v.label for v in unknown)
        _err(f"unclear, and this does not touch them: {names}")

    # What goes, and what puts it back, before anything does. On stderr with
    # the rest of the diagnostics, so stdout carries the result alone;
    # --quiet and --yes do not silence it.
    prune.plan(go, _err)

    # A Refused from here goes to _dispatch, which exits 3 for every refusal.
    # No terminal is one condition, so it is one code wherever it is met.
    if not args.yes and not prune.confirm(len(go)):
        _err("nothing removed")
        return 0

    failed = prune.sweep(go, _err)
    removed = [v.label for v in go]
    if args.json:
        print(json.dumps({"removed": removed, "failed": failed}, indent=2))
    elif failed:
        _err(
            render.err(
                f"{failed} of {len(go)} could not be removed; each said why above",
                RED,
            )
        )
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
    import json

    from . import new_branch
    from . import repo as R
    from .git import options

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
        _err(render.err(str(exc), RED))
        return 1

    if args.json:
        print(
            json.dumps(
                {"branch": started.branch, "base": started.base, "sha": started.sha},
                indent=2,
            )
        )
    elif not args.quiet:
        print(
            f"{render.out(started.branch, BOLD)} from "
            f"{R.ref_name(started.base)} at {started.sha}"
        )
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
    import json

    from . import new_branch
    from . import repo as R
    from . import rotate as rotate_mod
    from .git import options

    options.verbose = args.verbose
    if args.explain:
        return _explain()

    warn = None if args.quiet else _err
    if args.no_fetch and not args.quiet:
        _err("working from what is already here; refs may be stale (--no-fetch)")

    try:
        result = rotate_mod.rotate(fetch=not args.no_fetch, warn=warn)
    except new_branch.Refusal as exc:
        _err(render.err(str(exc), RED))
        return 1

    if isinstance(result, rotate_mod.CaughtUp):
        if args.json:
            print(json.dumps({"branch": result.branch, "at": result.at}, indent=2))
        elif not args.quiet:
            print(f"{render.out(result.branch, BOLD)} is at {result.at}")
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
        print(
            f"{render.out(result.branch, BOLD)} from "
            f"{R.ref_name(result.base)} at {result.sha}"
        )
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
    # For the shell completions, which must not hold a candidate of their own.
    # Hidden because a person has --list, which is the same answer formatted
    # for eyes.
    p.add_argument("--complete", action="store_true", help=argparse.SUPPRESS)
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
    p.add_argument(
        "--no-forge",
        action="store_true",
        help="decide from git alone; never ask the forge",
    )
    p.add_argument("-y", "--yes", action="store_true", help="do not ask")
    # Its own, not gwl's: assess skips the main checkout, so gwr can never act
    # on it and offering it would complete to "no worktree matches".
    p.add_argument("--complete", action="store_true", help=argparse.SUPPRESS)
    _add_cd_flags(p)


def _landed(args: argparse.Namespace, landed: wt_mod.Landed) -> int:
    """One destination on stdout, so `cd $(gwa x)` works."""
    import json

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
    from . import new_branch
    from . import worktree as wt_mod
    from .git import options

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
        _err(render.err(str(exc), RED))
        return 1
    if not args.quiet and not args.json:
        made = "on a new branch" if landed.created else "on the branch already here"
        _err(f"{render.err(landed.branch, BOLD)} {made}")
    return _landed(args, landed)


def run_list(args: argparse.Namespace) -> int:
    """Pick a worktree to stand in, the main checkout included.

    The main checkout is where a finished branch leaves you, so leaving it out
    of the list is leaving out the only destination that is always there. The
    one you are standing in is listed and never offered: picking it is the one
    answer that cannot take you anywhere.
    """
    import json

    from . import pick
    from . import repo as R
    from .git import options

    options.verbose = args.verbose
    if args.explain:
        return _explain()

    here = R.toplevel().out.strip()
    rows = [w for w in R.worktrees() if "bare" not in w.flags]

    if args.complete:
        # Every one of them, the current included: the shell does the
        # narrowing, and a completion that hides a candidate is worse than one
        # that offers a useless one.
        for w in rows:
            print(f"{w.label}\t{w.path}")
        return 0

    found = pick.matches(args.query, rows)

    if args.json:
        print(
            json.dumps(
                [
                    {
                        "branch": w.branch,
                        "path": w.path,
                        "main": w.main,
                        "here": _same(w.path, here),
                        "flags": sorted(w.flags),
                    }
                    for w in found
                ],
                indent=2,
            )
        )
        return 0

    if args.list:
        if not found:
            _err("no worktree matches")
            return 1
        width = max(len(w.label) for w in found)
        for w in found:
            mark = render.out("*", GREEN) if _same(w.path, here) else " "
            print(
                f"{mark} {render.out(w.label.ljust(width), BOLD)}  "
                f"{render.out(w.path, DIM)}"
            )
        return 0

    elsewhere = [w for w in found if not _same(w.path, here)]
    if not elsewhere:
        if not found:
            _err("no worktree matches")
            return 1
        if len(rows) < 2:
            _err("this is the only worktree; gwa NAME makes another")
            return 1
        _err(f"already in {render.err(found[0].label, BOLD)}")
        return 0

    # A query that narrows to one has said which. No query has not, even
    # when the repository holds exactly one other worktree. The one you are
    # standing in is shown above them, marked and unnumbered.
    chosen = pick.choose(
        elsewhere,
        _err,
        outright=bool(args.query),
        standing_in=next((w for w in found if _same(w.path, here)), None),
    )
    if chosen is None:
        _err("nothing picked")
        return 0
    print(chosen.path)
    return 0


def run_move(args: argparse.Namespace) -> int:
    from . import new_branch
    from . import worktree as wt_mod
    from .git import options

    options.verbose = args.verbose
    if args.explain:
        return _explain()
    if not args.name:
        _err(f"usage: {args.prog} NEW")
        return 2

    try:
        landed = wt_mod.move(args.name)
    except new_branch.Refusal as exc:
        _err(render.err(str(exc), RED))
        return 1
    if not args.quiet and not args.json:
        _err(f"renamed to {render.err(landed.branch, BOLD)}")
    return _landed(args, landed)


def run_remove(args: argparse.Namespace) -> int:
    import json

    from . import pick, prune
    from . import repo as R
    from . import worktree as wt_mod
    from .git import options

    options.verbose = args.verbose
    if args.explain:
        return _explain()

    if args.complete:
        # Every worktree this can reach, and no verdict: whether a branch is
        # finished is what gwr works out after you pick one, not what decides
        # whether its name can be typed. No fetch either, so TAB stays fast.
        for w in R.worktrees():
            if "bare" not in w.flags and not w.main:
                print(f"{w.label}\t{w.path}")
        return 0

    # The picker shows a path and a branch and no verdict, so nothing before
    # the answer needs the forge. Scanning without it turns one round trip per
    # worktree into at most one for the whole command.
    rows, _, head, head_branch, _ = _assess(
        args, judge=wt_mod.removable, ask_forge=False
    )
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

    # Now that there is one branch, the forge is worth a question: it is the
    # only thing that settles a branch merged as part of a stack.
    if chosen.verdict != prune.REMOVE and not args.no_forge:
        judged = wt_mod.removable(
            chosen.branch, head, head_branch, args.delete_ignored, ask_forge=True
        )
        if judged:
            chosen = judged[0]

    if chosen.verdict != prune.REMOVE and not args.force:
        # A finished branch held back by ignored files is not an unfinished
        # branch. Saying so would contradict the reason printed beside it,
        # and --force is the wrong answer to it: it deletes those files just
        # the same, and keeps a branch whose work already landed.
        state = "is finished" if chosen.ignored else "is not finished"
        _err(
            f"{render.err(chosen.label, BOLD)} {state}: "
            f"{render.err(chosen.why, YELLOW)}"
        )
        if not chosen.ignored:
            _err("pass --force to remove the worktree anyway; the branch is kept")
        return 1

    prune.plan([chosen], _err)
    if not args.yes and not prune.confirm(1):
        _err("nothing removed")
        return 0

    keep_branch = chosen.verdict != prune.REMOVE
    # --force keeps the branch: the worktree was in the way, the work was not.
    destination = wt_mod.remove(
        prune.Verdict(chosen.verdict, "", chosen.path, chosen.why)
        if keep_branch
        else chosen,
        _err,
    )
    if not args.quiet and not args.json:
        _err(f"removed {render.err(chosen.label, BOLD)}")
    if args.json:
        print(json.dumps({"removed": chosen.label, "path": destination}, indent=2))
        return 0
    if destination:
        print(destination)
    return 0


# --------------------------------------------------------------------------
# help
# --------------------------------------------------------------------------


def run_version(args: argparse.Namespace) -> int:
    """The version of the installed distribution."""
    print(__version__)
    return 0


def _listed(names: list[str]) -> str:
    """`a, b and c`, and `a` on its own when there is one."""
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} and {names[-1]}"


def run_help(args: argparse.Namespace) -> int:
    """Every name this package installs, and what each one answers."""
    print(f"worktrees {__version__}: git worktree commands that refuse to lose work")
    print()

    rows: list[Row] = [(Cell("COMMAND", DIM), Cell("GW", DIM), Cell("DOES", DIM))]
    for name, c in _COMMANDS.items():
        spelled = f"gw {name}"
        if c.aliases:
            spelled += " (" + ", ".join(c.aliases) + ")"
        rows.append(
            (
                # A row installing no binary has nothing for this column:
                # `gw version` is the only way to type it.
                Cell(f"{c.binary} {c.usage}".strip() or "\u2014", BOLD),
                Cell(spelled),
                Cell(c.about),
            )
        )
    print(table(rows))
    print()

    lands = [c.binary for c in _COMMANDS.values() if c.lands]
    print(
        f"{_listed(lands)} change the directory you are "
        "standing in.\nA binary cannot do that, so each needs the shell function "
        "of the same name\nfrom the plugin; the rest need nothing but $PATH."
    )
    print()
    # Which commands take the common flags is the table's answer, not a second
    # list here: a row with no flags takes none, and saying so in prose is how
    # a help text comes to promise a flag the program refuses.
    bare = [
        c.binary or f"gw {name}" for name, c in _COMMANDS.items() if c.flags is None
    ]
    scope = f"every command but {_listed(bare)}" if bare else "every command"
    print(
        "gwnb and gwrot start branches rather than worktrees. Both are alpha "
        f"and may go.\n\n--json, -q, -v and --explain work on {scope}, and\n"
        "`gw <command> --help` has the rest. A non-empty NO_COLOR turns the "
        "colour off."
    )
    return 0


def _complete_commands() -> int:
    """Every subcommand and shorthand, for the shell completions.

    Name and description, tab-separated, which is what fish reads directly
    and what zsh splits for `_describe`. It comes from the same table `gwh`
    prints, so a command added there needs no edit in either shell file.
    """
    for name, c in _COMMANDS.items():
        print(f"{name}\t{c.about}")
        for alias in c.aliases:
            print(f"{alias}\t{c.about}")
    return 0


# --------------------------------------------------------------------------
# the command table
# --------------------------------------------------------------------------


def _add_prune_flags(p: argparse.ArgumentParser) -> None:
    _add_assess_flags(p)
    p.add_argument(
        "-y", "--yes", action="store_true", help="do not ask before removing"
    )


class _Command:
    """One command under both its names, and every shorthand for it.

    The one place they are written down. `gw status`, `gw st`, `gw s` and
    `gws` reach the same function because this row says so, and `gw help`
    prints the row rather than a second list that can disagree with it.
    """

    __slots__ = ("about", "aliases", "binary", "flags", "lands", "run", "usage")

    run: Callable[[argparse.Namespace], int]
    flags: Callable[[argparse.ArgumentParser], None] | None
    about: str
    binary: str  # the console script, where the command installs one
    usage: str
    aliases: tuple[str, ...]
    lands: bool  # changes the caller's directory, so it needs a shim

    def __init__(
        self,
        run: Callable[[argparse.Namespace], int],
        flags: Callable[[argparse.ArgumentParser], None] | None,
        about: str,
        binary: str = "",
        usage: str = "",
        aliases: tuple[str, ...] = (),
        lands: bool = False,
    ) -> None:
        self.run = run
        self.flags = flags
        self.about = about
        self.binary = binary
        self.usage = usage
        self.aliases = aliases
        self.lands = lands


_COMMANDS: dict[str, _Command] = {
    "status": _Command(
        run_status,
        _add_assess_flags,
        "which worktrees are finished, and why",
        "gws",
        aliases=("s", "st"),
    ),
    "prune": _Command(
        run_prune,
        _add_prune_flags,
        "remove the ones status marks removable",
        "gwp",
        aliases=("p",),
    ),
    "add": _Command(
        run_add,
        _add_add_flags,
        "create a worktree and land you in it",
        "gwa",
        usage="NAME [BASE]",
        aliases=("a",),
        lands=True,
    ),
    "list": _Command(
        run_list,
        _add_list_flags,
        "pick a worktree and land you in it",
        "gwl",
        usage="[QUERY]",
        aliases=("l", "ls"),
        lands=True,
    ),
    "move": _Command(
        run_move,
        _add_move_flags,
        "rename this branch and move it",
        "gwm",
        usage="NEW",
        aliases=("m", "mv"),
        lands=True,
    ),
    "remove": _Command(
        run_remove,
        _add_remove_flags,
        "remove one whose branch is finished",
        "gwr",
        usage="[QUERY]",
        aliases=("rm",),
        lands=True,
    ),
    "new-branch": _Command(
        run_new_branch,
        _add_new_branch_flags,
        "branch off the head branch (alpha)",
        "gwnb",
        usage="NAME",
        aliases=("nb", "new"),
    ),
    "rotate": _Command(
        run_rotate,
        _add_rotate_flags,
        "the next branch in a series (alpha)",
        "gwrot",
        aliases=("rot",),
    ),
    "help": _Command(
        run_help,
        None,
        "every command and alias, this list",
        "gwh",
        aliases=("h",),
    ),
    # The one row with no console script. Asking a tool its version is done
    # through the tool's name, and a tenth binary for it would be a name
    # nobody types.
    "version": _Command(
        run_version,
        None,
        "the version this package installs",
        aliases=("v",),
    ),
}


def _canonical() -> dict[str, str]:
    """Every name and shorthand, mapped to the command it runs.

    A collision is a typo in the table above, and import is the cheapest
    moment to hear about it: a dict comprehension would keep the last one and
    leave a command reachable under a name that runs a different one.
    """
    found: dict[str, str] = {}
    for name, command in _COMMANDS.items():
        for alias in (name, *command.aliases):
            if alias in found:
                raise AssertionError(f"{alias} names both {found[alias]} and {name}")
            found[alias] = name
    return found


_CANONICAL = _canonical()


# --------------------------------------------------------------------------
# entry points
# --------------------------------------------------------------------------


_STATUS_HELP = "Say which of this repository's worktrees are finished, and why."
_PRUNE_HELP = "Remove the worktrees gws marks removable, and their branches."
_NEW_BRANCH_HELP = "Fetch, then branch NAME off the head branch and check it out."
_ROTATE_HELP = (
    "Start the next branch after this one, named <stem>-YYYY-MM-DD_NNN. On the "
    "head branch there is no chain to continue, so it catches that up to the "
    "remote instead."
)


def _parser(prog: str, description: str) -> argparse.ArgumentParser:
    return argparse.ArgumentParser(prog=prog, description=description)


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


def gwh(argv: list[str] | None = None) -> int:
    """One name that answers "what did this install?"."""
    p = _parser("gwh", "List every command and shorthand worktrees installs.")
    return _dispatch(p, argv, run_help)


def gw(argv: list[str] | None = None) -> int:
    """The short name. Its own prog, so a usage error names what was typed."""
    return main(argv, prog="gw")


def _unknown_command(prog: str, typed: str) -> int:
    """A name no row answers to, and the nearest one that does.

    argparse spells every command twice for this, once in a usage line and
    once in the choices, so the name worth reading arrives last. The pool is
    the same table `gwh` prints, plus `--help`, which people type as a
    command.
    """
    # difflib costs 0.8 ms to import and only a mistyped command needs it,
    # so the run that made the typo pays for it and no other run does.
    import difflib

    _err(f"{prog}: there is no command {typed!r}")
    pool = [*_CANONICAL, "--help"]
    near = difflib.get_close_matches(typed, pool, n=1)
    if near:
        _err(f"the closest is: {prog} {near[0]}")
    _err(f"{prog} help lists every command")
    return 2


def main(argv: list[str] | None = None, prog: str = "worktrees") -> int:
    """The CLI a shim and an agent call."""
    args_in = sys.argv[1:] if argv is None else argv
    if args_in and args_in[0] == "--complete":
        return _complete_commands()
    if (
        args_in
        and args_in[0].startswith("-")
        and args_in[0]
        not in (
            "-h",
            "--help",
        )
    ):
        # `worktrees --explain` is not about one command; status answers it.
        args_in = ["status", *args_in]
    if args_in and not args_in[0].startswith("-") and args_in[0] not in _CANONICAL:
        return _unknown_command(prog, args_in[0])

    p = _parser(prog, "Git worktree commands that refuse to lose work.")
    subs = p.add_subparsers(dest="command")
    for name in _needed(args_in):
        command = _COMMANDS[name]
        sub = subs.add_parser(name, aliases=list(command.aliases), help=command.about)
        if command.flags:
            command.flags(sub)
    return _dispatch(p, args_in, None)


def _needed(args_in: list[str]) -> list[str]:
    """Which subparsers this invocation has to have built.

    One, normally. Building all eleven and their shorthands is 1.5 ms of a
    47 ms run, and ten of them answer a question nobody asked.

    Two invocations need the whole set. `gw --help` prints the list, and a
    name is checked against `_CANONICAL` before this is reached, so anything
    else reaching the else-branch is argparse's to report.
    """
    if not args_in:
        return []  # `gw` on its own prints the help table and parses nothing
    if args_in[0] in _CANONICAL:
        return [_CANONICAL[args_in[0]]]
    return list(_COMMANDS)


def _dispatch(
    p: argparse.ArgumentParser,
    argv: list[str] | None,
    run: Callable[[argparse.Namespace], int] | None,
) -> int:
    render.setup()
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
    # into the private table for it. The alias the caller typed, not the
    # canonical name, so a usage line echoes what they wrote.
    command = getattr(args, "command", None)
    args.prog = f"{p.prog} {command}" if command else p.prog
    if run is None:
        if command is None:
            return run_help(args)
        run = _COMMANDS[_CANONICAL[command]].run
    try:
        return run(args)
    except _Stop as exc:
        _err(render.err(str(exc), RED))
        return exc.code
    except Refused as exc:
        _err(render.err(str(exc), RED))
        return 3
    except GitError as exc:
        _err(render.err(str(exc), RED))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
