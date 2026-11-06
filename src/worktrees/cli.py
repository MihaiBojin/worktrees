"""Argument parsing and output. Data on stdout, diagnostics on stderr."""

from __future__ import annotations

import argparse
import os
import sys
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


def _require_head() -> None:
    from . import repo as R

    resolved = R.head()
    if not resolved.ref:
        raise Refused(resolved.unresolved)


def _json(value: dict | list, *, indent: int = 2) -> str:
    import json

    from . import sync

    result = {"worktrees": value} if isinstance(value, list) else value
    return json.dumps({**result, **sync.envelope()}, indent=indent)


def _add_sync_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--json", action="store_true", help="sync state as data")
    p.add_argument("-q", "--quiet", action="store_true", help="omit the summary")
    p.add_argument("-v", "--verbose", action="store_true", help="print every command")


def run_sync(args: argparse.Namespace) -> int:
    from . import sync
    from .git import options

    options.verbose = args.verbose
    with progress.running(not args.quiet and not args.verbose):
        code, messages = sync.run()
    for message in messages:
        _err(render.fail(message))
    if args.json:
        print(_json({}))
    elif not code and not args.quiet:
        print("Sync complete.")
    return code


def _same(a: str, b: str) -> bool:
    """One directory under two spellings, or under a symlink."""
    return bool(a) and bool(b) and Path(a).resolve() == Path(b).resolve()


def _refuse_ambiguous(found: list[R.Worktree]) -> None:
    if len(found) > 1 and not sys.stdin.isatty():
        raise Refused(
            "ambiguous worktree name; use an exact path:\n"
            + "\n".join(w.path for w in found)
        )


def _verdict_table(rows: list[verdicts.Verdict]) -> str:
    """The assessment table for worktree previews and pruning."""

    head: Row = (
        Cell("BRANCH", BOLD),
        Cell("PATH", BOLD),
        Cell("VERDICT", BOLD),
        Cell("ELIGIBLE", BOLD),
        Cell("WHY", BOLD),
    )
    body: list[Row] = [
        (
            Cell(v.label, render.BRANCH),
            Cell(v.path, DIM),
            Cell(v.verdict, render.VERDICT.get(v.verdict, "")),
            Cell("yes" if v.eligible else "no"),
            Cell(v.why),
        )
        for v in rows
    ]
    return table([head, *body])


def _counted(rows: list[verdicts.Verdict]) -> str:
    """How many of each verdict, in the verdicts' own colours."""
    eligible = sum(v.eligible for v in rows)
    return render.counted([v.verdict for v in rows]) + f"; {eligible} eligible"


def add_status_flags(p: argparse.ArgumentParser) -> None:
    """Filter assessment rows by verdict."""
    p.add_argument(
        "--only",
        action="append",
        choices=render.VERDICTS,
        metavar="VERDICT",
        help="show only rows with this verdict (remove, empty, keep or unknown); "
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


def _add_delete_flags(p: argparse.ArgumentParser, *, branch: bool = False) -> None:
    p.add_argument(
        "--delete",
        action="append",
        choices=("empty", "stashes", "all")
        if branch
        else ("empty", "ignored-files", "stashes", "all"),
        default=[],
        metavar="VALUE",
        help="permit empty branches or stashes"
        if branch
        else "permit empty branches, ignored-files or stashes; "
        "all permits every category; repeat to combine",
    )


def _add_assess_flags(p: argparse.ArgumentParser) -> None:
    _add_delete_flags(p)
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
    """Resolve the local head ref and judge every worktree.

    The one path every command takes, so `prune` can only ever act on what
    `status` printed. `judge` is how `remove` asks for the one you stand in
    to be judged like any other, having stepped out of it first.
    """
    from . import repo as R
    from . import verdicts

    head, head_branch, _ = _assessment_head(args)

    only = getattr(args, "branch", "")
    permissions: frozenset[str] = getattr(args, "permissions", frozenset())
    keep_branch = getattr(args, "keep_branch", False)
    ignored = "ignored-files" in permissions
    records = R.worktrees()
    # Cached forge evidence can settle a branch whose stacked changes differ
    # from the head branch's final content.
    if ask_forge is None:
        ask_forge = True
    rows = (
        judge(
            only,
            head,
            head_branch,
            ignored,
            ask_forge=ask_forge,
            permissions=permissions,
            keep_branch=keep_branch,
        )
        if judge
        else verdicts.assess(
            records,
            only,
            head,
            head_branch,
            ignored,
            ask_forge=ask_forge,
            # Targeted removal supplies its own candidate assessment.
            include_main=True,
            permissions=permissions,
        )
    )
    return rows, verdicts.stale(records), head, head_branch, records


def _assessment_head(args: argparse.Namespace) -> tuple[str, str, str]:
    """Resolve the shared assessment target without fetching."""
    from . import repo as R

    remote = R.remote()
    resolved = R.head()
    return resolved.ref or "", resolved.branch, remote


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------


def run_preview(args: argparse.Namespace) -> int:
    """Say what is here. Nothing in this path removes anything."""

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
            _json(
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
                    # pass --delete ignored-files wants the count, not a sentence
                    # that happens to contain it, and the sha is what the
                    # verdict was formed against.
                    "verdicts": [
                        {
                            "verdict": v.verdict,
                            "eligible": v.eligible,
                            "proof": v.proof,
                            "request": v.request,
                            "protections": v.protections,
                            "stashes": v.stashes,
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
    """Preview the assessment or remove eligible worktrees after confirmation."""

    from . import prune, verdicts
    from .git import options

    options.verbose = args.verbose
    if args.explain:
        return _explain()

    if args.dry_run:
        return run_preview(args)

    # git's own bookkeeping for worktrees whose directories somebody removed
    # by hand. A stale record answers for a directory that is not there, and
    # neither command may propose removing one.
    _require_head()
    prune.worktree_prune()

    rows, _, _, _, _ = _assess(args)
    go = prune.removable(rows)
    unknown = [v for v in rows if v.verdict == verdicts.UNKNOWN]

    if not args.json:
        print(_verdict_table(rows), flush=True)

    if not go:
        # The reason each one stayed, here, rather than the name of the
        # command that would have printed it.
        if args.json:
            print(_json({"removed": [], "failed": 0}, indent=2))
        else:
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
    if not args.yes and not prune.confirm(len(go), never=prune.never_synced(go)):
        _err(render.note("nothing removed"))
        return 0

    outcomes = prune.sweep(go, _err)
    failed = sum(bool(o.failed) for o in outcomes)
    removed = [v.label for v, o in zip(go, outcomes, strict=True) if o.worktree_removed]
    if args.json:
        print(
            _json(
                {
                    "removed": removed,
                    "failed": failed,
                    "outcomes": [vars(o) for o in outcomes],
                },
                indent=2,
            )
        )
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
    p.add_argument("--json", action="store_true", help="the result as data")
    p.add_argument(
        "-q", "--quiet", action="store_true", help="omit summaries and progress"
    )
    p.add_argument(
        "-v", "--verbose", action="store_true", help="print every git command"
    )
    p.add_argument(
        "--explain", action="store_true", help="print every git command and exit"
    )


def run_new_branch(args: argparse.Namespace) -> int:

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

    try:
        started = new_branch.create(args.name)
    except new_branch.Refusal as exc:
        _err(render.fail(str(exc)))
        return 1

    if args.json:
        print(
            _json(
                {"branch": started.branch, "base": started.base, "sha": started.sha},
                indent=2,
            )
        )
    else:
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
    p.add_argument("--json", action="store_true", help="the result as data")
    p.add_argument(
        "-q", "--quiet", action="store_true", help="omit summaries and progress"
    )
    p.add_argument(
        "-v", "--verbose", action="store_true", help="print every git command"
    )
    p.add_argument(
        "--explain", action="store_true", help="print every git command and exit"
    )


def run_rotate(args: argparse.Namespace) -> int:

    from . import new_branch
    from . import repo as R
    from . import rotate as rotate_mod
    from .git import options

    options.verbose = args.verbose
    if args.explain:
        return _explain()

    try:
        result = rotate_mod.rotate()
    except new_branch.Refusal as exc:
        _err(render.fail(str(exc)))
        return 1

    if isinstance(result, rotate_mod.CaughtUp):
        if args.json:
            print(
                _json(
                    {
                        "branch": result.branch,
                        "at": result.at,
                        "previous_sha": result.previous_sha,
                        "sha": result.sha,
                    },
                    indent=2,
                )
            )
        else:
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
            _json(
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
    else:
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
    p.set_defaults(quiet=False, verbose=False)
    p.add_argument(
        "--explain", action="store_true", help="print every git command and exit"
    )


def _add_cd_output_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("-q", "--quiet", action="store_true", help="the path alone")
    p.add_argument(
        "-v", "--verbose", action="store_true", help="print every git command"
    )


def _add_add_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("name", nargs="?", default="", metavar="NAME", help="the branch")
    p.add_argument(
        "base", nargs="?", default="", metavar="BASE", help="what to branch from"
    )
    _add_cd_flags(p)
    _add_cd_output_flags(p)


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
    _add_cd_output_flags(p)


def _add_remove_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "query", nargs="?", default="", metavar="PATH|QUERY", help="which one"
    )
    p.add_argument(
        "--keep-branch",
        action="store_true",
        help="remove the checkout and retain its branch and stashes",
    )
    _add_delete_flags(p)
    p.add_argument("-y", "--yes", action="store_true", help="do not ask")
    # Its own, not gwl's: assess skips the main checkout, so gwr can never act
    # on it and offering it would complete to "no worktree matches".
    p.add_argument("--complete", action="store_true", help=argparse.SUPPRESS)
    _add_cd_flags(p)
    _add_cd_output_flags(p)


def _landed(args: argparse.Namespace, landed: wt_mod.Landed) -> int:
    """One destination on stdout, so `cd $(gwa x)` works."""

    if args.json:
        print(
            _json(
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
        landed = wt_mod.add(args.name, args.base, warn=warn)
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

    from . import layout, pick
    from . import repo as R
    from .git import options

    options.verbose = args.verbose
    if args.explain:
        return _explain()

    here = R.toplevel().value
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
            _json(
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

    if args.query:
        _refuse_ambiguous(offered)

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

    from . import layout, pick, prune
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

    # Assess cached evidence and protections before offering candidates.
    _require_head()
    rows, _, _, _, records = _assess(args, judge=wt_mod.removable, ask_forge=True)
    worktrees = [R.Worktree(v.path, "", v.branch, frozenset()) for v in rows]
    found = worktrees
    if args.query:
        found = [w for w in worktrees if _same(w.path, args.query)]
        if not found:
            found = [
                w for w in worktrees if args.query in (layout.name_of(w.path), w.branch)
            ]
        _refuse_ambiguous(found)
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
    here = R.toplevel().value
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

    if not chosen.eligible:
        _err(
            render.fail(
                f"{render.branch(chosen.label)} kept: {chosen.why}; "
                "--keep-branch retains the branch"
            )
        )
        return 1

    prune.plan([chosen], _err, keep_branch=args.keep_branch)
    if not args.yes and not prune.confirm(1, never=prune.never_synced([chosen])):
        _err(render.note("nothing removed"))
        return 0
    if _same(chosen.path, here):
        if not sys.stdin.isatty():
            raise Refused(
                f"not a terminal; removing current worktree {chosen.path} "
                "requires a second confirmation"
            )
        if prune._prompt(
            render.ask(f"remove current worktree {chosen.path}?", "[y/N]")
        ).strip().lower() not in ("y", "yes"):
            _err(render.note("nothing removed"))
            return 0
    outcome = wt_mod.remove(chosen, _err)
    if args.json:
        print(
            _json(
                {
                    "removed": chosen.label if outcome.worktree_removed else None,
                    "path": outcome.destination,
                    "outcome": vars(outcome),
                },
                indent=2,
            )
        )
    elif outcome.destination:
        print(outcome.destination)
    return int(bool(outcome.failed))


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
    print("gw NAME selects a worktree; subcommands take precedence.")
    print("gwp! skips confirmation; gwr! deletes ignored files.")
    print("gwp!! deletes empty branches, ignored files and branch stashes.")
    text, same = plugin_state()
    print(o(text, "" if same else YELLOW))
    print()
    print(
        f"{names(['gwbc', 'gwrot'])} start branches rather than worktrees."
        "\n\nOutput flags depend on the command. "
        f"`{o('gw <command> --help', BOLD)}` lists them.\n"
        f"A non-empty {o('NO_COLOR', BOLD)} turns the colour off."
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
    add_status_flags(p)
    p.epilog = "gwp!! deletes empty branches, ignored files and branch stashes."
    p.add_argument("--dry-run", action="store_true", help="report without deleting")
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

    Every alias reaches the row's function. Help prints these rows.
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
    "sync": _Command(
        run_sync, _add_sync_flags, "refresh remote refs and forge evidence"
    ),
    "branch": _Command(
        run_branch, _add_branch_flags, "create, assess and delete local branches"
    ),
    "prune": _Command(
        run_prune,
        _add_prune_flags,
        "assess worktrees and remove eligible ones",
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
    "help": _Command(
        run_help,
        None,
        "every command and alias, this list",
        "gwh",
        aliases=("h",),
    ),
    # Version is available through the tool's name without a separate binary.
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


_PRUNE_HELP = "Assess worktrees and remove eligible ones, or preview with --dry-run."
_NEW_BRANCH_HELP = "Branch NAME off the head ref and check it out, without fetching."
_ROTATE_HELP = (
    "Start the next branch after this one, named <stem>-YYYY-MM-DD_NNN. On the "
    "head branch there is no chain to continue, so it catches that up to the "
    "remote instead. Exits 1 when either would change nothing."
)


class _Parser(argparse.ArgumentParser):
    """argparse, with its help on a stream the caller can see.

    A command that changes directory prints its destination on stdout, and
    its shell function captures that stream, so its help goes to stderr. The
    rest keep argparse's stdout, where `gwp -h | less` reads it.
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

    render.setup()  # reached before _dispatch, which does this for the rest
    lines = [f"{prog}: there is no command {typed!r}"]
    pool = [*_CANONICAL, "--help"]
    near = difflib.get_close_matches(typed, pool, n=1)
    if near:
        lines.append(f"the closest is: {prog} {near[0]}")
    lines.append(f"{prog} help lists every command")
    _err(render.fail("\n".join(lines)))
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
        args_in = ["prune", "--dry-run", *args_in]
    if not args_in:
        args_in = ["prune", "--dry-run"]
    if args_in and not args_in[0].startswith("-") and args_in[0] not in _CANONICAL:
        if len(args_in) == 1:
            return gwl(args_in)
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
        if getattr(args, "quiet", False) and getattr(args, "verbose", False):
            raise _Stop("--quiet and --verbose cannot be combined", 2)
        if run is run_prune and args.dry_run and args.verbose:
            raise _Stop("--verbose is not available in preview mode", 2)
        values = getattr(args, "delete", [])
        if getattr(args, "keep_branch", False) and any(
            value != "ignored-files" for value in values
        ):
            raise _Stop("--keep-branch accepts only --delete ignored-files", 2)
        args.permissions = frozenset(
            ("empty", "ignored-files", "stashes") if "all" in values else values
        )
        if run is run_prune and args.only and not args.dry_run:
            raise _Stop("--only requires --dry-run", 2)
        if (
            run not in (run_help, run_version, run_sync)
            and not getattr(args, "explain", False)
            and not getattr(args, "complete", False)
            and not (hasattr(args, "name") and not args.name)
            and not (run is run_branch and not getattr(args, "branch_command", None))
        ):
            from . import repo as R
            from . import sync
            from .git import options

            options.verbose = getattr(args, "verbose", False)
            R.require_offline_git()
            if not getattr(args, "json", False) and R.toplevel().value:
                for notice in sync.notices(getattr(args, "quiet", False)):
                    _err(render.note(notice))
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
