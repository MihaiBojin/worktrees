"""Argument parsing and output. Data on stdout, diagnostics on stderr."""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from . import __version__, progress, render
from .errors import GitError, Refused
from .render import BLUE, BOLD, DIM, GREEN, RED, YELLOW, Cell, Row, table

if TYPE_CHECKING:  # names used in annotations, which never run
    from . import repo as R
    from . import verdicts
    from . import worktree as wt_mod


def _err(text: str) -> None:
    progress.line(text)


def _warn(text: str) -> None:
    _err(render.warn(text))


def _same(a: str, b: str) -> bool:
    """One directory under two spellings, or under a symlink."""
    return bool(a) and bool(b) and Path(a).resolve() == Path(b).resolve()


def _verdict_table(rows: list[verdicts.Verdict]) -> str:
    """The table `gws` prints, which `gwp` prints too rather than name it."""

    head: Row = (
        Cell("BRANCH", BOLD),
        Cell("PATH", BOLD),
        Cell("VERDICT", BOLD),
        Cell("WHY", BOLD),
    )
    body: list[Row] = [
        (
            Cell(v.label, render.BRANCH),
            Cell(v.path, DIM),
            Cell(v.verdict, render.VERDICT.get(v.verdict, "")),
            Cell(v.why),
        )
        for v in rows
    ]
    return table([head, *body])


def _counted(rows: list[verdicts.Verdict]) -> str:
    """How many of each verdict, in the verdicts' own colours."""
    from . import prune, verdicts

    # `removable` rather than the verdict, so the count is what gwp removes.
    go = {id(v) for v in prune.removable(rows)}
    return render.counted(
        [
            verdicts.REMOVE
            if id(v) in go
            else verdicts.UNKNOWN
            if v.verdict == verdicts.UNKNOWN
            else verdicts.KEEP
            for v in rows
        ]
    )


def add_status_flags(p: argparse.ArgumentParser, refresh: str) -> None:
    """What `gws` and `gwbs` take beyond what `gwp` and `gwbd` share: a
    short -f, and a filter. Not on the two that remove, where -f would read
    as force."""
    p.add_argument("-f", dest="force_refresh", action="store_true", help=refresh)
    p.add_argument(
        "--only",
        action="append",
        choices=render.VERDICTS,
        metavar="VERDICT",
        help="show only rows with this verdict (remove, keep or unknown); "
        "repeat for more",
    )


def _only(args: argparse.Namespace, rows: list) -> list:
    """The rows --only asks for, all of them without it."""
    wanted = getattr(args, "only", None)
    return [v for v in rows if v.verdict in wanted] if wanted else rows


def _offerable(path: str) -> bool:
    """Can this path be one line of `--complete` output?

    The protocol is one row a line, `label<TAB>path`, and both shells split
    records on the newline. So a path holding one arrives as two rows and its
    tail becomes a candidate of its own. A tab is fine: both split a row at
    the first one, so a tab inside the path lands in the description.

    A filename may hold any byte but NUL and `/`. Nothing here makes such a
    path, since `gwa` derives it from a branch name and git refuses a newline
    in a ref, so this only ever skips a worktree added by hand.
    """
    return "\n" not in path


def _explain() -> int:
    """Every git command the program can issue."""
    from .git import RULES, commands

    rows: list[Row] = [(Cell(""), Cell("COMMAND", BOLD), Cell("GIT", BOLD))]
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
        "--force-refresh",
        action="store_true",
        help="fetch and ask the forge again, whatever is kept",
    )
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


# A fetch younger than this is used as it stands; --force-refresh fetches.
_FRESH = 10 * 60


def _should_fetch(args: argparse.Namespace) -> bool:
    """Not under --no-fetch, and not when any worktree fetched under 30
    minutes ago, unless --force-refresh. A fetch is most of what a run costs,
    and refs a few minutes old answer the same questions."""
    from . import repo as R

    if args.no_fetch:
        return False
    fetched = None if args.force_refresh else R.fetched_at()
    if fetched is None or time.time() - fetched >= _FRESH:
        return True
    if not args.quiet:
        _err(
            render.note(
                f"fetched {int((time.time() - fetched) // 60)} min ago; using "
                "those refs (--force-refresh fetches now)"
            )
        )
    return False


def _assess(
    args: argparse.Namespace,
    judge: Callable[..., list[verdicts.Verdict]] | None = None,
    ask_forge: bool | None = None,
) -> tuple[list[verdicts.Verdict], list[R.Worktree], str, str, list[R.Worktree]]:
    """`_judge_all`, with a spinner on stderr saying what it is checking."""
    with progress.running(not args.quiet and not args.verbose):
        return _judge_all(args, judge, ask_forge)


def _judge_all(
    args: argparse.Namespace,
    judge: Callable[..., list[verdicts.Verdict]] | None,
    ask_forge: bool | None,
) -> tuple[list[verdicts.Verdict], list[R.Worktree], str, str, list[R.Worktree]]:
    """Fetch, resolve the head branch, and judge every worktree.

    The one path every command takes, so `prune` can only ever act on what
    `status` printed. `judge` is how `remove` asks for the one you stand in
    to be judged like any other, having stepped out of it first.
    """
    from . import repo as R
    from . import verdicts
    from .git import options

    options.refresh = args.force_refresh
    head, head_branch, _ = _assessment_head(args)

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


def _assessment_head(args: argparse.Namespace) -> tuple[str, str, str]:
    """Resolve the shared assessment target and report offline operation."""
    from . import repo as R

    remote = R.remote()
    # A skipped fetch still leaves the forge asked: it is what settles a
    # stacked branch, and it is not what the fetch refreshed.
    online = _should_fetch(args) if remote else not args.no_fetch
    if online:
        # An unreachable remote is not a reason to refuse to answer. It lands
        # where --no-fetch already goes, and says so, and the head-branch
        # ladder is told not to spend a second round trip on the same remote.
        progress.say(f"fetching {remote}")
        if remote and not R.fetch(remote):
            online = False
            # The forge is reached over the network the fetch just failed on,
            # and `gh` waits 30 seconds before saying so, once for every
            # branch git could not call merged. It is asked for an answer git
            # cannot give, not for one it is about to fail to give too.
            args.no_forge = True
            if not args.quiet:
                _warn(
                    f"{render.ref(remote)} could not be fetched; judging from the refs "
                    "already here, and not asking the forge"
                )
    elif args.no_fetch and not args.quiet:
        # A fetch reused because it is recent has already said so.
        _warn("using the refs already here; they may be stale (--no-fetch)")

    progress.say("finding the head branch")
    head, warning = R.head_ref(remote, online=online)
    if warning and not args.quiet:
        _warn(warning)
    if not head:
        raise _Stop("cannot tell which branch this repository branches from", 2)
    head_branch = R.ref_name(head)
    if remote:
        head_branch = head_branch.removeprefix(remote + "/")

    return head, head_branch, remote


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

    # Every repository beside this one shares the same `.worktrees` root, and
    # nothing else says which of the directories there are somebody else's.
    # `worktrees` puts the main checkout first, so records[0] is it.
    main = records[0].path if records else ""
    others = layout.neighbours(main, [w.path for w in records]) if main else 0

    if args.json:
        print(
            json.dumps(
                {
                    "head": head,
                    "stale": [w.path for w in stale],
                    # How many worktrees under the shared root are a
                    # sibling repository's. Nothing here reads or removes
                    # them; a caller counting directories needs to know
                    # they are not all this repository's.
                    "neighbours": others,
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
                        for v in _only(args, rows)
                    ],
                },
                indent=2,
            )
        )
        return 0

    print(_verdict_table(_only(args, rows)))

    if args.quiet:
        return 0

    # Every row counted, --only or not, so a filtered table still says what
    # it is not showing.
    print()
    print(_counted(rows))
    if len(rows) < 2:
        _err(render.note("no worktrees besides the main checkout; gwa NAME makes one"))
    if stale:
        _warn(
            f"{len(stale)} stale record(s) for directories that are gone; "
            "gwp clears them"
        )
    if others:
        root = layout.worktrees_root(main)
        _err(
            render.note(
                f"{others} worktree(s) under {root} belong to another repository"
            )
        )
    if unknown:
        names = ", ".join(render.branch(v.label) for v in unknown)
        _warn(f"unclear, and gwp does not touch these: {names}")
    if go:
        _err(render.note(f"gwp removes the {len(go)} marked removable"))
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
                    _err(
                        render.note(
                            "no worktrees besides the main checkout; gwa NAME makes one"
                        )
                    )
        return 0

    if unknown and not args.quiet:
        names = ", ".join(render.branch(v.label) for v in unknown)
        _warn(f"unclear, and this does not touch them: {names}")

    # What goes, and what puts it back, before anything does. On stderr with
    # the rest of the diagnostics, so stdout carries the result alone;
    # --quiet and --yes do not silence it.
    prune.plan(go, _err)

    # A Refused from here goes to _dispatch, which exits 3 for every refusal.
    # No terminal is one condition, so it is one code wherever it is met.
    if not args.yes and not prune.confirm(len(go)):
        _err(render.note("nothing removed"))
        return 0

    failed = prune.sweep(go, _err)
    removed = [v.label for v in go]
    if args.json:
        print(json.dumps({"removed": removed, "failed": failed}, indent=2))
    elif failed:
        _err(
            render.fail(
                f"{failed} of {len(go)} could not be removed; each said why above"
            )
        )
    else:
        names = ", ".join(render.out(v.label, render.BRANCH) for v in go)
        print(f"removed {len(go)} worktree(s): {names}")
    return 1 if failed else 0


# --------------------------------------------------------------------------
# branch create
# --------------------------------------------------------------------------


def _add_new_branch_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "name", nargs="?", default="", metavar="NAME", help="the branch to start"
    )
    p.add_argument(
        "--no-fetch", action="store_true", help="branch off what is already here"
    )
    p.add_argument(
        "--force-refresh",
        action="store_true",
        help="fetch even when the last fetch is under 10 minutes old",
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
        # The program the caller typed, so `worktrees branch create` does not
        # answer with the name of its own alias.
        return _needs(args, "NAME")

    warn = None if args.quiet else _warn
    if args.no_fetch and not args.quiet:
        _warn("branching from what is already here; refs may be stale (--no-fetch)")

    try:
        started = new_branch.create(args.name, fetch=_should_fetch(args), warn=warn)
    except new_branch.Refusal as exc:
        _err(render.fail(str(exc)))
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
            f"{render.out(started.branch, render.BRANCH)} from "
            f"{render.out(R.ref_name(started.base), BLUE)} at "
            f"{render.out(started.sha, YELLOW)}"
        )
    return 0


# --------------------------------------------------------------------------
# rotate
# --------------------------------------------------------------------------


def _add_rotate_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--no-fetch", action="store_true", help="work from what is already here"
    )
    p.add_argument(
        "-f",
        "--force-refresh",
        action="store_true",
        help="fetch even when the last fetch is under 10 minutes old",
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

    warn = None if args.quiet else _warn
    if args.no_fetch and not args.quiet:
        _warn("working from what is already here; refs may be stale (--no-fetch)")

    try:
        result = rotate_mod.rotate(fetch=_should_fetch(args), warn=warn)
    except new_branch.Refusal as exc:
        _err(render.fail(str(exc)))
        return 1

    if isinstance(result, rotate_mod.CaughtUp):
        if args.json:
            print(
                json.dumps(
                    {
                        "branch": result.branch,
                        "at": result.at,
                        "previous_sha": result.previous_sha,
                        "sha": result.sha,
                    },
                    indent=2,
                )
            )
        elif not args.quiet:
            print(
                _was_now(
                    (result.branch, result.previous_sha, ""),
                    (
                        result.branch,
                        result.sha,
                        f"fast-forwarded by {_commits(result.count)} to {result.at}",
                    ),
                )
            )
        return 0

    if args.json:
        print(
            json.dumps(
                {
                    "branch": result.branch,
                    "stem": result.stem,
                    "base": result.base,
                    "sha": result.sha,
                    "previous": result.previous,
                    "previous_sha": result.previous_sha,
                },
                indent=2,
            )
        )
    elif not args.quiet:
        base = R.ref_name(result.base)
        kept = (
            f"kept, {_commits(result.ahead)} not on {base}"
            if result.ahead
            else "kept, no commits of its own"
        )
        print(
            _was_now(
                (result.previous, result.previous_sha, kept),
                (result.branch, result.sha, f"new, from {base}"),
            )
        )
    return 0


def _was_now(was: tuple[str, str, str], now: tuple[str, str, str]) -> str:
    """Where HEAD stood before and stands after: branch, sha and a note."""
    return table(
        [
            (
                Cell("was", DIM),
                Cell(was[0], render.BRANCH),
                Cell(was[1], YELLOW),
                Cell(was[2], DIM),
            ),
            (
                Cell("now", BOLD),
                Cell(now[0], render.BRANCH),
                Cell(now[1], YELLOW),
                Cell(now[2], DIM),
            ),
        ]
    )


def _commits(n: int) -> str:
    return f"{n} commit" if n == 1 else f"{n} commits"


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
    p.add_argument(
        "--force-refresh",
        action="store_true",
        help="fetch even when the last fetch is under 10 minutes old",
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
        "--force-refresh",
        action="store_true",
        help="fetch and ask the forge again, whatever is kept",
    )
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
        return _needs(args, "NAME")

    warn = None if args.quiet else _warn
    try:
        landed = wt_mod.add(args.name, args.base, fetch=_should_fetch(args), warn=warn)
    except new_branch.Refusal as exc:
        _err(render.fail(str(exc)))
        return 1
    if not args.quiet and not args.json:
        made = "on a new branch" if landed.created else "on the branch already here"
        _err(render.note(f"{render.branch(landed.branch)} {made}"))
    return _landed(args, landed)


def run_list(args: argparse.Namespace) -> int:
    """Pick a worktree to stand in, the main checkout included.

    The main checkout is where a finished branch leaves you, so leaving it out
    of the list is leaving out the only destination that is always there, and
    it is offered even from inside it. Any other worktree you are standing in
    is listed and never offered: picking it is the one answer that cannot take
    you anywhere.
    """
    import json

    from . import layout, pick
    from . import repo as R
    from .git import options

    options.verbose = args.verbose
    if args.explain:
        return _explain()

    here = R.toplevel().out.strip()
    rows = [w for w in R.worktrees() if "bare" not in w.flags]

    if args.complete:
        # Every one of them the protocol can carry, the current included: the
        # shell does the narrowing, and a completion that hides a candidate is
        # worse than one that offers a useless one.
        for w in rows:
            if _offerable(w.path):
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

    # Nothing can answer a picker without a terminal, so a script gets every
    # match, a single one included, and nothing is selected.
    if args.list or not sys.stdin.isatty():
        if not found:
            _err(render.fail("no worktree matches"))
            return 1
        # The picker's columns, without its numbers: `*` marks where you are.
        # Never coloured: like --json, it is what a script reads.
        head: Row = ("", "WORKTREE", "BRANCH", "PATH")
        body: list[Row] = [
            (
                "*" if _same(w.path, here) else "",
                layout.name_of(w.path),
                w.label,
                w.path,
            )
            for w in found
        ]
        print(table([head, *body]))
        return 0

    if not found:
        _err(render.fail("no worktree matches"))
        return 1
    if len(rows) < 2:
        _err(render.fail("this is the only worktree; gwa NAME makes another"))
        return 1
    # The main checkout is offered even to somebody standing in it: from a
    # subdirectory it is the way back to the top. Any other worktree you
    # stand in is shown above the rest, marked and unnumbered.
    offered = [w for w in found if w.main or not _same(w.path, here)]
    if not offered:
        _err(render.note(f"already in {render.branch(found[0].label)}"))
        return 0

    # Which repository this is, above the list, so nobody has to run
    # `git remote -v` to find out. Only where the picker is drawn: --list and
    # --json are what a script reads, and a query that narrows to one takes it
    # without showing anything.
    if not (args.query and len(offered) == 1):
        for name, url in R.remote_urls():
            _err(f"{render.ref(name)}: {url}")

    # A query that narrows to one has said which. No query has not, even
    # when the repository holds exactly one other worktree.
    chosen = pick.choose(
        offered,
        outright=bool(args.query),
        shown=[("*", w) for w in found if w not in offered],
        here=next((w for w in offered if _same(w.path, here)), None),
    )
    if chosen is None:
        _err(render.note("nothing picked"))
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
        return _needs(args, "NEW")

    try:
        landed = wt_mod.move(args.name)
    except new_branch.Refusal as exc:
        _err(render.fail(str(exc)))
        return 1
    if not args.quiet and not args.json:
        _err(render.note(f"renamed to {render.branch(landed.branch)}"))
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
            if "bare" not in w.flags and not w.main and _offerable(w.path):
                print(f"{w.label}\t{w.path}")
        return 0

    # The picker shows a path and a branch and no verdict, so nothing before
    # the answer needs the forge. Scanning without it turns one round trip per
    # worktree into at most one for the whole command.
    rows, _, head, head_branch, records = _assess(
        args, judge=wt_mod.removable, ask_forge=False
    )
    found = pick.matches(
        args.query, [R.Worktree(v.path, "", v.branch, frozenset()) for v in rows]
    )
    by_path = {v.path: v for v in rows}
    candidates = [by_path[w.path] for w in found if w.path in by_path]

    if not candidates:
        _err(
            render.fail(
                "no worktree matches"
                if args.query
                else "no worktrees besides the main checkout"
            )
        )
        return 1

    # The main checkout is listed and never offered: `git worktree remove`
    # refuses it whatever flag it is given. The worktree you stand in is
    # highlighted first, since it is the one you most likely came to remove.
    here = R.toplevel().out.strip()
    offered = [R.Worktree(v.path, "", v.branch, frozenset()) for v in candidates]
    picked = pick.choose(
        offered,
        shown=[("*" if _same(w.path, here) else "", w) for w in records if w.main],
        at=next((w for w in offered if _same(w.path, here)), None),
    )
    if picked is None:
        _err(render.note("nothing picked"))
        return 0
    chosen = by_path[picked.path]

    # Now that there is one branch, the forge is worth a question: it is the
    # only thing that settles a branch merged as part of a stack.
    if chosen.verdict != prune.REMOVE and not args.no_forge:
        with progress.running(not args.quiet and not args.verbose):
            judged = wt_mod.removable(
                chosen.branch, head, head_branch, args.delete_ignored, ask_forge=True
            )
        if judged:
            chosen = judged[0]

    if (
        chosen.verdict != prune.REMOVE
        and chosen.ignored
        and not args.force
        and not args.yes
        and sys.stdin.isatty()
    ):
        # Somebody at a terminal can consent to what --delete-ignored would
        # have. The plan below names every path before the question, and
        # --yes never stands in for that consent.
        _warn(
            f"{render.branch(chosen.label)} is finished, but holds "
            f"{chosen.ignored} ignored path(s); they go with it"
        )
        chosen = prune.Verdict(
            prune.REMOVE, chosen.branch, chosen.path, chosen.why, 0, chosen.sha
        )

    if chosen.verdict != prune.REMOVE and not args.force:
        # A finished branch held back by ignored files is not an unfinished
        # branch. Saying so would contradict the reason printed beside it,
        # and --force is the wrong answer to it: it deletes those files just
        # the same, and keeps a branch whose work already landed.
        state = "is finished" if chosen.ignored else "is not finished"
        why = f"{render.branch(chosen.label)} {state}: {chosen.why}"
        if chosen.ignored:
            _err(render.fail(why))
            return 1
        if args.yes or not sys.stdin.isatty():
            _err(
                render.fail(
                    f"{why}\npass --force to remove the worktree anyway; "
                    "the branch is kept"
                )
            )
            return 1
        # Somebody at a terminal is asked instead, the way --force would have
        # answered: the checkout goes and the branch stays.
        _warn(f"{why}\nremoving the worktree keeps the branch")

    prune.plan([chosen], _err)
    if not args.yes and not prune.confirm(1):
        _err(render.note("nothing removed"))
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
        _err(render.note(f"removed {render.branch(chosen.label)}"))
    if args.json:
        print(json.dumps({"removed": chosen.label, "path": destination}, indent=2))
        return 0
    if destination:
        print(destination)
    return 0


# --------------------------------------------------------------------------
# help
# --------------------------------------------------------------------------


# Exported by conf.d/worktrees.fish and worktrees.plugin.zsh, set to the
# release each came from. scripts/bump_version.py writes both.
PLUGIN_VERSION = "WORKTREES_PLUGIN_VERSION"


def _release(version: str) -> tuple[int, ...] | None:
    """`x.y.z` as numbers to compare, or None for anything else, such as the
    `0+unknown` of a source tree nothing installed."""
    parts = version.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None
    return tuple(int(p) for p in parts)


def plugin_state() -> tuple[str, bool]:
    """What the shell plugin here says about its release, against this
    package's, and whether the two are the same release.

    A shell without the variable has no plugin loaded, or one from before the
    plugin carried it. Nothing here can tell those two apart.
    """
    plugin = os.environ.get(PLUGIN_VERSION, "")
    if not plugin:
        return (
            "No shell plugin here says which release it is: none is loaded,\n"
            "or it is older than the release that started saying so.",
            False,
        )
    if plugin == __version__:
        return (
            f"The shell plugin here is {plugin}, the same release as these commands.",
            True,
        )
    ours, theirs = _release(__version__), _release(plugin)
    if ours and theirs and theirs < ours:
        return (
            f"The shell plugin here is {plugin}, older than these commands.\n"
            f"Pin it to v{__version__}: https://github.com/MihaiBojin/worktrees#install",
            False,
        )
    if ours and theirs:
        return (
            f"The shell plugin here is {plugin}, newer than these commands.\n"
            "Upgrade them with `uv tool upgrade git-worktrees`.",
            False,
        )
    return (
        f"The shell plugin here is {plugin}, and these commands are {__version__}.",
        False,
    )


def run_version(args: argparse.Namespace) -> int:
    """The version of the installed distribution on stdout, which is what a
    script reads, and the shell plugin's on stderr."""
    print(__version__)
    text, same = plugin_state()
    _err(render.note(text) if same else render.warn(text))
    return 0


def _listed(names: list[str]) -> str:
    """`a, b and c`, and `a` on its own when there is one."""
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} and {names[-1]}"


def run_help(args: argparse.Namespace) -> int:
    """Every name this package installs, and what each one answers."""
    o = render.out
    print(
        f"{o('worktrees', BOLD)} {o(__version__, YELLOW)}: git worktree commands "
        "that refuse to lose work"
    )
    print()

    # The binary green, the subcommand blue, what goes after either dim, so
    # each column reads as one kind of thing. Plain text off a terminal.
    rows: list[Row] = [(Cell("COMMAND", BOLD), Cell("GW", BOLD), Cell("DOES", BOLD))]
    for name, c in command_rows():
        aliases = f" ({', '.join(c.aliases)})" if c.aliases else ""
        rows.append(
            (
                # A row installing no binary has nothing for this column:
                # `gw version` is the only way to type it.
                Cell.of((c.binary, BOLD + GREEN), (f" {c.usage}", DIM))
                if c.binary and c.usage
                else Cell(c.binary or "\u2014", BOLD + GREEN if c.binary else DIM),
                Cell.of(("gw ", DIM), (name, BLUE), (aliases, DIM)),
                Cell(c.about),
            )
        )
    print(table(rows))
    print()

    def names(binaries: list[str]) -> str:
        return _listed([o(b, BOLD + GREEN) for b in binaries])

    lands = [c.binary for _, c in command_rows() if c.lands]
    print(
        f"{names(lands)} change the directory you are "
        "standing in.\nA binary cannot do that, so each needs the shell function "
        "of the same name\nfrom the plugin; the rest need nothing but $PATH."
    )
    text, same = plugin_state()
    print(o(text, "" if same else YELLOW))
    print()
    # Which commands take the common flags is the table's answer, not a second
    # list here: a row with no flags takes none, and saying so in prose is how
    # a help text comes to promise a flag the program refuses.
    bare = [c.binary or f"gw {name}" for name, c in command_rows() if c.flags is None]
    scope = f"every command but {names(bare)}" if bare else "every command"
    flags = _listed([o(f, BOLD) for f in ("--json", "-q", "-v", "--explain")])
    print(
        f"{names(['gwbc', 'gwrot'])} start branches rather than worktrees."
        f"\n\n{flags} work on {scope}, and\n"
        f"`{o('gw <command> --help', BOLD)}` has the rest. A non-empty "
        f"{o('NO_COLOR', BOLD)} turns the colour off."
    )
    return 0


def _complete_commands() -> int:
    """Every subcommand and shorthand, for the shell completions.

    Name and description, tab-separated, which is what fish reads directly
    and what zsh splits for `_describe`. It comes from the same table `gw -h`
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


def _add_status_flags(p: argparse.ArgumentParser) -> None:
    _add_assess_flags(p)
    add_status_flags(p, "the same as --force-refresh")


def _add_prune_flags(p: argparse.ArgumentParser) -> None:
    _add_assess_flags(p)
    p.add_argument(
        "-y", "--yes", action="store_true", help="do not ask before removing"
    )


def _add_branch_status_flags(p: argparse.ArgumentParser) -> None:
    from .branch_cli import status_flags

    status_flags(p)


def _add_branch_delete_flags(p: argparse.ArgumentParser) -> None:
    from .branch_cli import delete_flags

    delete_flags(p)


def _add_branch_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--complete", action="store_true", help=argparse.SUPPRESS)
    subs = p.add_subparsers(dest="branch_command")
    for name, command in _BRANCH_COMMANDS.items():
        child = subs.add_parser(name, help=command.about)
        if command.flags:
            command.flags(child)


def run_branch_status(args: argparse.Namespace) -> int:
    from .branch_cli import status

    return status(args)


def run_branch_delete(args: argparse.Namespace) -> int:
    from .branch_cli import delete

    return delete(args)


def run_branch(args: argparse.Namespace) -> int:
    if args.branch_command:
        return _BRANCH_COMMANDS[args.branch_command].run(args)
    if args.complete:
        for name, command in _BRANCH_COMMANDS.items():
            print(f"{name}\t{command.about}")
        return 0
    p = _parser(args.prog, "Create, assess and delete local branches.")
    _add_branch_flags(p)
    p.print_help()
    return 0


class _Command:
    """One command under both its names, and every shorthand for it.

    The one place they are written down. `gw status`, `gw st`, `gw s` and
    `gws` reach the same function because this row says so, and `gw -h`
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


_BRANCH_COMMANDS: dict[str, _Command] = {
    "create": _Command(
        run_new_branch,
        _add_new_branch_flags,
        "branch off the head branch",
        "gwbc",
        usage="NAME",
    ),
    "status": _Command(
        run_branch_status,
        _add_branch_status_flags,
        "which branches are finished, and why",
        "gwbs",
        usage="[NAME ...]",
    ),
    "delete": _Command(
        run_branch_delete,
        _add_branch_delete_flags,
        "delete local branches proved finished",
        "gwbd",
        usage="[NAME ...]",
    ),
}


_COMMANDS: dict[str, _Command] = {
    "branch": _Command(
        run_branch, _add_branch_flags, "create, assess and delete local branches"
    ),
    "status": _Command(
        run_status,
        _add_status_flags,
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
    "rotate": _Command(
        run_rotate,
        _add_rotate_flags,
        "the next branch in a series",
        "gwrot",
        aliases=("rot",),
    ),
    # The one row with no console script. Asking a tool its version is done
    # through the tool's name, and a binary for it would be a name nobody
    # types.
    "version": _Command(
        run_version,
        None,
        "the version this package installs",
        aliases=("v",),
    ),
}


def command_rows() -> list[tuple[str, _Command]]:
    """Every executable command with its full subcommand path."""
    return [
        (name, command) for name, command in _COMMANDS.items() if name != "branch"
    ] + [(f"branch {name}", command) for name, command in _BRANCH_COMMANDS.items()]


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
    "remote instead. Exits 1 when either would change nothing."
)


class _Parser(argparse.ArgumentParser):
    """argparse, with its help on a stream the caller can see.

    A command that changes directory prints its destination on stdout, and
    its shell function captures that stream, so its help goes to stderr. The
    rest keep argparse's stdout, where `gws -h | less` reads it.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Subparsers are built from this class too and their defaults win, so
        # every command finds the parser that names it, for `_needs`.
        self.set_defaults(parser=self)

    def print_help(self, file=None) -> None:
        if file is None:
            lands = {c.binary for c in _COMMANDS.values() if c.lands}
            file = sys.stderr if self.prog in lands else sys.stdout
        # argparse decides colour by asking whether sys.stdout is a terminal,
        # whatever stream the help goes to, so it is asked about this one.
        saved, sys.stdout = sys.stdout, file
        try:
            text = self.format_help()
        finally:
            sys.stdout = saved
        file.write(text)


def _parser(prog: str, description: str) -> argparse.ArgumentParser:
    return _Parser(prog=prog, description=description)


def _needs(args: argparse.Namespace, what: str) -> int:
    """A missing argument: say which, then the help that explains it.

    Both on stderr, since a shell function prints nothing it captured from a
    command that failed.
    """
    _err(render.fail(f"{args.prog} needs {what}"))
    _err("")
    args.parser.print_help(sys.stderr)
    return 2


def gws(argv: list[str] | None = None) -> int:
    """Read-only. There is no flag here that removes anything."""
    p = _parser("gws", _STATUS_HELP)
    _add_status_flags(p)
    return _dispatch(p, argv, run_status)


def gwp(argv: list[str] | None = None) -> int:
    """Asks before it acts, unless --yes."""
    p = _parser("gwp", _PRUNE_HELP)
    _add_prune_flags(p)
    return _dispatch(p, argv, run_prune)


def gwbs(argv: list[str] | None = None) -> int:
    command = _BRANCH_COMMANDS["status"]
    p = _parser(command.binary, command.about)
    _add_branch_status_flags(p)
    return _dispatch(p, argv, command.run)


def gwbd(argv: list[str] | None = None) -> int:
    command = _BRANCH_COMMANDS["delete"]
    p = _parser(command.binary, command.about)
    _add_branch_delete_flags(p)
    return _dispatch(p, argv, command.run)


def gwbc(argv: list[str] | None = None) -> int:
    """Checking out needs no shell either."""
    p = _parser("gwbc", _NEW_BRANCH_HELP)
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


def gw(argv: list[str] | None = None) -> int:
    """The short name. Its own prog, so a usage error names what was typed."""
    return main(argv, prog="gw")


def _unknown_command(prog: str, typed: str) -> int:
    """A name no row answers to, and the nearest one that does.

    argparse spells every command twice for this, once in a usage line and
    once in the choices, so the name worth reading arrives last. The pool is
    the same table `gw -h` prints, plus `--help`, which people type as a
    command.
    """
    # difflib costs 0.8 ms to import and only a mistyped command needs it,
    # so the run that made the typo pays for it and no other run does.
    import difflib

    render.setup()  # reached before _dispatch, which does this for the rest
    lines = [f"{prog}: there is no command {typed!r}"]
    pool = [*_CANONICAL, "--help"]
    near = difflib.get_close_matches(typed, pool, n=1)
    if near:
        lines.append(f"the closest is: {prog} {near[0]}")
    lines.append(f"{prog} -h lists every command")
    _err(render.fail("\n".join(lines)))
    return 2


def main(argv: list[str] | None = None, prog: str = "worktrees") -> int:
    """The CLI a shim and an agent call."""
    args_in = sys.argv[1:] if argv is None else argv
    if args_in and args_in[0] == "--complete":
        return _complete_commands()
    if args_in and args_in[0] in ("-h", "--help"):
        render.setup()  # reached before _dispatch, which does this for the rest
        return run_help(argparse.Namespace())
    if args_in and args_in[0].startswith("-"):
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
    """The one subparser this invocation has to have built.

    Building every one and its shorthands is 1.5 ms of a 47 ms run, and all
    but one answer a question nobody asked. `main` has already answered `-h`
    and refused a name no row answers to, so a first argument here names a
    command.
    """
    # `gw` on its own prints the help table and parses nothing
    return [_CANONICAL[args_in[0]]] if args_in else []


def _dispatch(
    p: argparse.ArgumentParser,
    argv: list[str] | None,
    run: Callable[[argparse.Namespace], int] | None,
) -> int:
    render.setup()
    raw = sys.argv[1:] if argv is None else argv
    # Worktree previews use status; branch deletion also has --dry-run.
    branch_delete = run is run_branch_delete or raw[:2] == ["branch", "delete"]
    if "--dry-run" in raw and not branch_delete:
        _err(
            render.fail(
                "there is no --dry-run: gws reports, and gwp asks before removing"
            )
        )
        return 2
    args = p.parse_args(raw)
    # argparse gives a subparser its own prog; rebuild it rather than reach
    # into the private table for it. The alias the caller typed, not the
    # canonical name, so a usage line echoes what they wrote.
    command = getattr(args, "command", None)
    args.prog = f"{p.prog} {command}" if command else p.prog
    if getattr(args, "branch_command", None):
        args.prog += f" {args.branch_command}"
    if run is None:
        if command is None:
            return run_help(args)
        run = _COMMANDS[_CANONICAL[command]].run
    try:
        return run(args)
    except KeyboardInterrupt:
        _err("")
        return 130
    except _Stop as exc:
        _err(render.fail(str(exc)))
        return exc.code
    except Refused as exc:
        _err(render.fail(str(exc)))
        return 3
    except GitError as exc:
        _err(render.fail(str(exc)))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
