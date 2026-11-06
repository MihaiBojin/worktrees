"""Argument parsing and output. Data on stdout, diagnostics on stderr."""

from __future__ import annotations

import argparse
import contextlib
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


def _same(a: str, b: str) -> bool:
    """One directory under two spellings, or under a symlink."""
    return bool(a) and bool(b) and Path(a).resolve() == Path(b).resolve()


def _verdict_table(rows: list[verdicts.Verdict], plain: bool = False) -> str:
    """The table `gwp` prints. `plain` is `gwp --list`'s, never
    coloured, since like --json it is what a script reads."""
    if plain:
        return table(
            [
                ("BRANCH", "PATH", "VERDICT", "WHY"),
                *((v.label, v.path, v.verdict, v.why) for v in rows),
            ]
        )
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


def add_status_flags(p: argparse.ArgumentParser) -> None:
    """The `--only` filter `gwp` and `gwbs` take."""
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
    head: tuple[R.Head, str],
    judge: Callable[..., list[verdicts.Verdict]] | None = None,
) -> tuple[list[verdicts.Verdict], list[R.Worktree], str, str, list[R.Worktree]]:
    """`_judge_all`, with a spinner on stderr saying what it is checking."""
    with progress.running(not args.quiet and not args.verbose):
        return _judge_all(args, head, judge)


def _judge_all(
    args: argparse.Namespace,
    head: tuple[R.Head, str],
    judge: Callable[..., list[verdicts.Verdict]] | None,
) -> tuple[list[verdicts.Verdict], list[R.Worktree], str, str, list[R.Worktree]]:
    """Judge every worktree against `_assessment_head`'s answer, or with
    `verdicts.unresolved` when that does not resolve.

    The one path every command takes, so `prune` acts only on what it
    printed. `judge` is how `remove` asks for the one you stand in to be
    judged like any other, having stepped out of it first.
    """
    from . import repo as R
    from . import verdicts

    found, head_branch = head
    ref = found.ref

    ignored = getattr(args, "delete_ignored", False)
    records = R.worktrees()
    rows = (
        verdicts.unresolved(records, head_branch, found.problem())
        if not found.resolves
        else judge(ref, head_branch, ignored)
        if judge
        else verdicts.assess(
            records,
            ref,
            head_branch,
            ignored,
            # `gwp` reports; `gwr` acts, and takes this list as its
            # candidates, so it asks through `judge` and gets it without.
            include_main=True,
        )
    )
    return rows, verdicts.stale(records), ref, head_branch, records


def _assessment_head(args: argparse.Namespace) -> tuple[R.Head, str]:
    """The head ref, shown, and the branch name it stands for."""
    from . import repo as R

    remote = R.remote()
    progress.say("finding the head branch")
    head = R.head_ref(remote)
    _shower(args)(head)
    head_branch = head.name
    if remote:
        head_branch = head_branch.removeprefix(remote + "/")
    return head, head_branch


def _shower(args: argparse.Namespace) -> Callable[[R.Head], None]:
    """What prints the sync warning and the head line: on stderr, never
    beside --json. Under -q the never-synced line stays and the stale one
    goes. The head line is never printed for a head that does not resolve,
    which the failure names instead, and under -q only when the head was
    inferred, since that one is a guess. The head is kept on `args` for
    `_envelope`."""
    from datetime import UTC, datetime

    from . import sync

    def show(head: R.Head) -> None:
        args.found_head = head
        if getattr(args, "json", False):
            return
        record = head.synced
        if record is None and head.remote:
            _warn(sync.NEVER)
        elif record and not args.quiet:
            stale = sync.stale(record, datetime.now(UTC))
            if stale:
                _warn(stale)
        if not head.resolves or (args.quiet and head.source != "inferred"):
            return
        detail = f"; {head.also} also exists" if head.also else ""
        _err(render.note(f"Head: {render.ref(head.name)} ({head.source}{detail})"))

    return show


def _envelope(args: argparse.Namespace) -> dict[str, object]:
    """What every --json result starts with: when each part of the last
    sync succeeded, and the head ref with its source. A command that read
    no head reads it here."""
    from . import repo as R

    head = getattr(args, "found_head", None) or R.head_ref(R.remote())
    synced = head.synced or {}
    return {
        "lastSync": {"refs": synced.get("refs"), "forge": synced.get("forge")},
        "head": {"ref": head.ref if head.resolves else None, "source": head.source},
    }


def _require(head: R.Head) -> None:
    """A command that changes something refuses without a head ref."""
    if not head.resolves:
        raise _Stop(head.problem(), 1)


def _neighbours(records: list[R.Worktree]) -> int:
    """How many worktrees under the shared root are a sibling repository's.

    Every repository beside this one shares the same `.worktrees` root, and
    nothing else says which of the directories there are somebody else's.
    `worktrees` puts the main checkout first, so records[0] is it.
    """
    from . import layout

    main = records[0].path if records else ""
    return layout.neighbours(main, [w.path for w in records]) if main else 0


def _assessment(
    args: argparse.Namespace,
    rows: list[verdicts.Verdict],
    stale: list[R.Worktree],
    records: list[R.Worktree],
) -> dict[str, object]:
    """The object `gwp --json` prints."""
    return {
        **_envelope(args),
        "stale": [w.path for w in stale],
        # Nothing here reads or removes a neighbour; a caller counting
        # directories needs to know they are not all this repository's.
        "neighbours": _neighbours(records),
        # `ignored` and `sha` are fields rather than facts to parse back out
        # of `why`. A caller deciding whether to pass --delete-ignored wants
        # the count, not a sentence that happens to contain it, and the sha
        # is what the verdict was formed against.
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
    }


def _report(
    args: argparse.Namespace,
    rows: list[verdicts.Verdict],
    stale: list[R.Worktree],
    records: list[R.Worktree],
) -> None:
    """The table, the counts and the notes `gwp` prints. Under
    `--list` the table alone, uncoloured, and the notes on stderr."""
    from . import layout, verdicts

    plain = getattr(args, "list", False)
    print(_verdict_table(_only(args, rows), plain=plain))
    if args.quiet:
        return

    # Every row counted, --only or not, so a filtered table still says what
    # it is not showing.
    if not plain:
        print()
        print(_counted(rows))
    if len(rows) < 2:
        _err(render.note("no worktrees besides the main checkout; gwa NAME makes one"))
    if stale:
        _warn(
            f"{len(stale)} stale record(s) for directories that are gone; "
            "a gwp that removes something clears them"
        )
    others = _neighbours(records)
    if others:
        root = layout.worktrees_root(records[0].path)
        _err(
            render.note(
                f"{others} worktree(s) under {root} belong to another repository"
            )
        )
    unknown = [v for v in rows if v.verdict == verdicts.UNKNOWN]
    if unknown:
        names = ", ".join(render.branch(v.label) for v in unknown)
        _warn(f"unclear, and gwp does not touch these: {names}")


# --------------------------------------------------------------------------
# prune
# --------------------------------------------------------------------------


def run_prune(args: argparse.Namespace) -> int:
    """Print the assessment, then remove what it marks removable: having
    asked, at a terminal, or under --yes. Anything else only reports."""
    import json

    from . import prune
    from .git import options

    options.verbose = args.verbose
    if args.explain:
        return _explain()

    asks = sys.stdin.isatty() and not args.json and not args.list
    head = _assessment_head(args)
    if args.yes or asks:
        # Before anything is judged, so a refusal is the whole of the output.
        _require(head[0])
    elif not head[0].resolves:
        _err(render.fail(head[0].problem()))

    rows, stale, _, _, records = _assess(args, head)
    go = prune.removable(rows)
    errors = int(not head[0].resolves or any(v.error for v in rows))
    result = _assessment(args, rows, stale, records) if args.json else {}

    if not args.json:
        _report(args, rows, stale, records)
        # The table reaches a pipe ahead of the disclosure and the prompt.
        sys.stdout.flush()

    if not go or not (args.yes or asks):
        if args.yes and args.json:
            result |= {"removed": [], "failed": 0}
        if args.json:
            print(json.dumps(result, indent=2))
        if not go:
            _err(render.note("nothing to remove"))
        else:
            _err(render.note(f"{len(go)} would be removed; gwp --yes removes them"))
        return errors

    # What goes, and what puts it back, before anything does. On stderr with
    # the rest of the diagnostics, so stdout carries the result alone;
    # --quiet and --yes do not silence it.
    never = prune.plan(go, _err)
    if not args.yes and not prune.confirm(len(go), never=never):
        _err(render.note("nothing removed"))
        return errors

    failed = prune.sweep(go, _err)
    if args.json:
        result |= {"removed": [v.label for v in go], "failed": failed}
        print(json.dumps(result, indent=2))
    elif failed:
        _err(
            render.fail(
                f"{failed} of {len(go)} could not be removed; each said why above"
            )
        )
    elif args.list:
        print(f"removed {len(go)} worktree(s): {', '.join(v.label for v in go)}")
    else:
        names = ", ".join(render.out(v.label, render.BRANCH) for v in go)
        print(f"removed {len(go)} worktree(s): {names}")
    return 1 if failed or errors else 0


# --------------------------------------------------------------------------
# branch create
# --------------------------------------------------------------------------


def _add_new_branch_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "name", nargs="?", default="", metavar="NAME", help="the branch to start"
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

    try:
        started = new_branch.create(args.name, show=_shower(args))
    except new_branch.Refusal as exc:
        _err(render.fail(str(exc)))
        return 1

    if args.json:
        print(
            json.dumps(
                {
                    **_envelope(args),
                    "branch": started.branch,
                    "base": started.base,
                    "sha": started.sha,
                },
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

    try:
        result = rotate_mod.rotate(show=_shower(args))
    except new_branch.Refusal as exc:
        _err(render.fail(str(exc)))
        return 1

    if isinstance(result, rotate_mod.CaughtUp):
        if args.json:
            print(
                json.dumps(
                    {
                        **_envelope(args),
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
                    **_envelope(args),
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


def _add_sync_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("-q", "--quiet", action="store_true", help="say nothing on success")
    p.add_argument(
        "-v", "--verbose", action="store_true", help="print every git command"
    )
    p.add_argument(
        "--explain", action="store_true", help="print every git command and exit"
    )


def run_sync(args: argparse.Namespace) -> int:
    """Fetch the default remote, ask it for its HEAD and the forge for every
    branch tip, and record the answers."""
    from . import repo as R
    from . import sync
    from .git import options

    options.verbose = args.verbose
    if args.explain:
        return _explain()

    names = R.remotes().lines
    if not names:
        if not args.quiet:
            _err(render.note("no remote, so nothing to sync"))
        return 0
    remote = R.remote()
    if not remote:
        stated = R.config_get("checkout.defaultRemote").out.strip()
        why = (
            f"checkout.defaultRemote names {stated}, which is none of them"
            if stated
            else "nothing says which to sync"
        )
        raise _Stop(
            f"{_listed(names)} are all remotes, and {why}.\n"
            "Pick one with: git config checkout.defaultRemote <name>",
            1,
        )
    for step in (R.fetch, R.set_head_auto):
        done = step(remote)
        if not done:
            raise _Stop(
                f"{render.ref(remote)} could not be synced:\n{done.err.strip()}", 1
            )
    refs = sync.now()
    asked, evidence, failed = sync.ask(remote, sync.read())
    sync.write(remote, refs, asked, evidence)
    if not args.quiet:
        head = R.ref_name(R.symbolic_ref(f"refs/remotes/{remote}/HEAD").out.strip())
        _err(render.note(f"synced {render.ref(remote)}; head is {render.ref(head)}"))
    for line in failed:
        _err(render.fail(line))
    return 1 if failed else 0


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
    p.add_argument("-y", "--yes", action="store_true", help="do not ask")
    # Its own, not gw list's: assess skips the main checkout, so gwr can never act
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
                    **_envelope(args),
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
        landed = wt_mod.add(args.name, args.base, warn=warn, show=_shower(args))
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
                {
                    **_envelope(args),
                    "worktrees": [
                        {
                            "branch": w.branch,
                            "path": w.path,
                            "main": w.main,
                            "here": _same(w.path, here),
                            "flags": sorted(w.flags),
                        }
                        for w in found
                    ],
                },
                indent=2,
            )
        )
        return 0

    if not found:
        return _no_match(args.prog.split()[0], args.query)

    # Nothing can answer a picker without a terminal, so a script gets every
    # match, a single one included, and nothing is selected.
    if args.list or not sys.stdin.isatty():
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
        # whether its name can be typed, so TAB stays fast.
        for w in R.worktrees():
            if "bare" not in w.flags and not w.main and _offerable(w.path):
                print(f"{w.label}\t{w.path}")
        return 0

    head = _assessment_head(args)
    _require(head[0])
    rows, _, _, _, records = _assess(args, head, judge=wt_mod.removable)
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

    if chosen.error:
        _err(
            render.fail(
                f"{render.branch(chosen.label)} could not be judged: {chosen.why}"
            )
        )
        return 1

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

    never = prune.plan([chosen], _err)
    if not args.yes and not prune.confirm(1, never=never):
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
        print(
            json.dumps(
                {**_envelope(args), "removed": chosen.label, "path": destination},
                indent=2,
            )
        )
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
        binary = _typed_as(name, c)
        rows.append(
            (
                # `gw version` installs no binary and has nothing for this
                # column: the subcommand is the only way to type it.
                Cell.of((binary, BOLD + GREEN), (f" {c.usage}", DIM))
                if binary and c.usage
                else Cell(binary or "\u2014", BOLD + GREEN if binary else DIM),
                Cell.of(("gw ", DIM), (name, BLUE), (aliases, DIM)),
                Cell(c.about),
            )
        )
    print(table(rows))
    print()

    def names(binaries: list[str]) -> str:
        return _listed([o(b, BOLD + GREEN) for b in binaries])

    lands = [_typed_as(name, c) for name, c in command_rows() if c.lands]
    print(
        f"{o('gw', BOLD + GREEN)} with no subcommand runs "
        f"{o('gw list', BOLD + GREEN)}.\n\n{names(lands)} change the directory you are "
        "standing in.\nA binary cannot do that, so each needs the shell function "
        "of the same name\nfrom the plugin; the rest need nothing but $PATH."
    )
    text, same = plugin_state()
    print(o(text, "" if same else YELLOW))
    print()
    # Which commands take the common flags is the table's answer, not a second
    # list here: a row with no flags takes none, and saying so in prose is how
    # a help text comes to promise a flag the program refuses.
    common = ("--json", "-q", "-v", "--explain")
    bare = [c.binary or f"gw {name}" for name, c in command_rows() if c.flags is None]
    scope = f"every command but {names(bare)}" if bare else "every command"
    flags = _listed([o(f, BOLD) for f in common])
    short = []
    for name, c in command_rows():
        if c.flags is not None:
            p = argparse.ArgumentParser(add_help=False)
            c.flags(p)
            # argparse's own index of the flags a parser takes.
            missing = [f for f in common if f not in p._option_string_actions]
            if missing:
                label = c.binary or f"gw {name}"
                short.append(f"{names([label])} takes no {_listed(missing)}")
    print(
        f"{names(['gwbc', 'gwrot'])} start branches rather than worktrees."
        f"\n\n{flags} work on {scope}" + "".join(f";\n{s}" for s in short) + ", and\n"
        f"`{o('gw <command> --help', BOLD)}` has the rest. A non-empty "
        f"{o('NO_COLOR', BOLD)} turns the colour off."
    )
    return 0


def _typed_as(name: str, c: _Command) -> str:
    """The name a person types without a subcommand: the console script, or
    `gw` itself for `gw list`, which is what `gw` alone runs."""
    return c.binary or ("gw" if name == "list" else "")


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


def _add_prune_flags(p: argparse.ArgumentParser) -> None:
    _add_assess_flags(p)
    add_status_flags(p)
    p.add_argument(
        "-l",
        "--list",
        action="store_true",
        help="print the table uncoloured and ask nothing",
    )
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

    The one place they are written down. `gw prune`, `gw p` and `gwp`
    reach the same function because this row says so, and `gw -h`
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
    "prune": _Command(
        run_prune,
        _add_prune_flags,
        "remove the worktrees proved finished",
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
    # No console script: `gw` with no subcommand runs this one.
    "list": _Command(
        run_list,
        _add_list_flags,
        "pick a worktree and land you in it",
        usage="[QUERY]",
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
    "sync": _Command(
        run_sync,
        _add_sync_flags,
        "fetch the default remote and ask the forge",
        "gws",
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


_PRUNE_HELP = (
    "Say which of this repository's worktrees are finished, and why. At a "
    "terminal, ask before removing the "
    "worktrees it marks removable, and their branches; elsewhere only report, "
    "unless --yes."
)
_NEW_BRANCH_HELP = "Branch NAME off the head branch and check it out."
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
            # `gw list` too: the `gw` function captures it, and no other
            # subcommand.
            lands = {c.binary for c in _COMMANDS.values() if c.lands}
            lands |= {"gw list", "worktrees list"}
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
    command = _COMMANDS["sync"]
    p = _parser(command.binary, command.about)
    _add_sync_flags(p)
    return _dispatch(p, argv, command.run)


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


def _no_match(prog: str, query: str) -> int:
    """No worktree answers to the query, and the command it is closest to.

    `gw ARGS` runs `gw list ARGS` whenever ARGS does not start with a
    command, so a mistyped command arrives here as a query. The pool is the
    full names `gw -h` prints, plus `--help`, which people type as a command.
    Shorthands stay out: `ls` is nearer `s` than `list` by difflib's count.
    """
    # difflib costs 0.8 ms to import and only a query that matched nothing
    # needs it, so no other run pays for it.
    import difflib

    lines = ["no worktree matches"]
    near = difflib.get_close_matches(query, [*_COMMANDS, "--help"], n=1)
    if near:
        lines.append(f"the closest command is: {prog} {near[0]}")
        lines.append(f"{prog} -h lists every command")
    _err(render.fail("\n".join(lines)))
    return 1


def main(argv: list[str] | None = None, prog: str = "worktrees") -> int:
    """The CLI a shim and an agent call."""
    args_in = sys.argv[1:] if argv is None else argv
    if args_in and args_in[0] == "--complete":
        return _complete_commands()
    if args_in and args_in[0] in ("-h", "--help"):
        # On stderr, since `gw` changes directory and its function captures
        # stdout. Decided after the swap, so colour follows the stream it
        # reaches.
        with contextlib.redirect_stdout(sys.stderr):
            render.setup()
            return run_help(argparse.Namespace())
    if not args_in or args_in[0] not in _CANONICAL:
        # A query, a flag or nothing at all: `gw ARGS` is `gw list ARGS`.
        args_in = ["list", *args_in]

    p = _parser(prog, "Git worktree commands that refuse to lose work.")
    subs = p.add_subparsers(dest="command")
    for name in _needed(args_in):
        command = _COMMANDS[name]
        sub = subs.add_parser(
            name,
            aliases=list(command.aliases),
            help=command.about,
            description=command.about,
        )
        if command.flags:
            command.flags(sub)
    return _dispatch(p, args_in, None)


def _needed(args_in: list[str]) -> list[str]:
    """The one subparser this invocation has to have built.

    Building every one and its shorthands is 1.5 ms of a 47 ms run, and all
    but one answer a question nobody asked. `main` has already answered `-h`
    and put `list` in front of anything else, so the first argument here
    names a command.
    """
    return [_CANONICAL[args_in[0]]]


def _dispatch(
    p: argparse.ArgumentParser,
    argv: list[str] | None,
    run: Callable[[argparse.Namespace], int] | None,
) -> int:
    render.setup()
    raw = sys.argv[1:] if argv is None else argv
    # gwp previews worktrees; branch deletion has a --dry-run of its own.
    branch_delete = run is run_branch_delete or raw[:2] == ["branch", "delete"]
    if "--dry-run" in raw and not branch_delete:
        _err(
            render.fail(
                "there is no --dry-run: gwp reports without a terminal and asks at one"
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
        run = _COMMANDS[_CANONICAL[args.command]].run
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
