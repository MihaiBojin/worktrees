"""Argument parsing and output. Data on stdout, diagnostics on stderr."""

from __future__ import annotations

import argparse
import os
import shlex
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from . import __version__, progress, render
from .errors import GitError, Refused
from .render import BLUE, BOLD, DIM, GREEN, RED, YELLOW, Cell, Row, table

if TYPE_CHECKING:  # names used in annotations, which never run
    from . import fast_forward, verdicts
    from . import repo as R
    from . import worktree as wt_mod


_NO_WORKTREES = "no worktrees besides the main checkout; gw add NAME makes one"


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
    from . import repo as R
    from . import sync
    from .git import options

    if sync.offline():
        raise Refused(f"gw sync uses the network, and {sync.OFFLINE} is set")
    options.verbose = args.verbose
    with progress.running(not args.quiet and not args.verbose):
        code, failures, warnings = sync.run()
    for message in failures:
        _err(render.fail(message))
    if not args.quiet:
        for message in warnings:
            _err(render.warn(message))
    if args.json:
        print(_json({}))
    elif not args.quiet:
        if not code:
            print("Sync complete." if R.remotes().lines else "No remote to sync.")
        _err(render.note(sync.head_line(R.head())))
    return code


def _refresh(args: argparse.Namespace) -> None:
    """Sync first when the shared evidence is due, and carry on if it fails.

    Its failures are warnings, since the command still runs on the evidence
    it has; quiet and JSON hide them, as they hide the stale line.
    """
    from . import sync

    try:
        due = sync.due()
    except ValueError as exc:
        raise _Stop(str(exc), 2) from None
    if not due:
        return
    quiet = getattr(args, "quiet", False)
    with progress.running(not quiet and not getattr(args, "verbose", False)):
        _, failures, warnings = sync.run()
    if not quiet and not getattr(args, "json", False):
        for message in (*failures, *warnings):
            _warn(message)


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


def _verdict_data(rows: list[verdicts.Verdict]) -> list[dict]:
    """Assessment fields shared by metadata and removal results."""
    return [
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
            "ignoredPaths": list(v.ignored_paths),
            "sha": v.sha,
            "undo": v.undo,
        }
        for v in rows
    ]


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
    path, since `gw add` derives it from a branch name and git refuses a newline
    in a ref, so this only ever skips a worktree added by hand.
    """
    return "\n" not in path


def _explain() -> int:
    """Every git command the program can issue."""
    from . import (  # noqa: F401
        branches,
        fast_forward,
        merged,
        new_branch,
        prune,
        repo,
        verdicts,
        worktree,
    )
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
        help="permit empty branches or stashes; all permits both; repeat to combine"
        if branch
        else "permit empty branches, ignored-files or stashes; "
        "all permits every category; repeat to combine",
    )


def _add_assess_flags(p: argparse.ArgumentParser) -> None:
    _add_delete_flags(p)
    p.add_argument(
        "--json", action="store_true", help="assess as JSON; --yes approves removal"
    )
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
                    # `ignored`, `ignoredPaths` and `sha` are fields rather
                    # than facts to parse back out of `why`. A caller deciding
                    # whether to pass --delete ignored-files wants the count
                    # and the paths, not a sentence that happens to contain
                    # them, and the sha is what the verdict was formed against.
                    "verdicts": _verdict_data(_only(args, rows)),
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
        _err(render.note(_NO_WORKTREES))
    if stale:
        _warn(
            f"{len(stale)} stale record(s) for directories that are gone; "
            "approved pruning clears them"
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
        _warn(f"unclear, and gw prune does not touch these: {names}")
    if go:
        _err(render.note(f"gw prune --yes removes the {len(go)} marked removable"))
    return 0


# --------------------------------------------------------------------------
# prune
# --------------------------------------------------------------------------


def run_prune(args: argparse.Namespace) -> int:
    """Assess worktrees and remove eligible targets after approval."""

    from . import prune, verdicts
    from .git import options

    options.verbose = args.verbose
    if args.explain:
        return _explain()

    if args.table:
        render.out.on = False
    if args.only or (
        not args.yes and (args.json or args.table or not sys.stdin.isatty())
    ):
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
            print(
                _json(
                    {"verdicts": _verdict_data(rows), "removed": [], "failed": 0},
                    indent=2,
                )
            )
        else:
            if not args.quiet:
                print()
                print(f"nothing to remove; {_counted(rows)}")
                if len(rows) < 2:
                    _err(render.note(_NO_WORKTREES))
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
                    "verdicts": _verdict_data(rows),
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
# fast-forward
# --------------------------------------------------------------------------


def _add_fast_forward_flags(p: argparse.ArgumentParser) -> None:
    # Not `name`: _dispatch reads an empty `name` as a missing argument and
    # skips the preflight, and this one is optional.
    p.add_argument(
        "new_name",
        nargs="?",
        default="",
        metavar="NAME",
        help="the branch a finished one moves to; a dated copy of its name "
        "when omitted",
    )
    p.add_argument(
        "--from",
        dest="start",
        default="",
        metavar="REF",
        help="start that branch from this branch or commit, not the head ref",
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


def run_fast_forward(args: argparse.Namespace) -> int:

    from . import fast_forward as ff
    from . import new_branch
    from .git import options

    options.verbose = args.verbose
    if args.explain:
        return _explain()

    try:
        moved = ff.fast_forward(args.new_name, args.start)
    except new_branch.Refusal as exc:
        _err(render.fail(str(exc)))
        return 1

    if args.json:
        print(_json(moved.data(), indent=2))
    if moved.result == ff.CONFLICT:
        # On stderr whatever the format, as every failure is: --json has
        # already put the same facts on stdout.
        _stopped(moved)
        return 1
    if not args.json:
        print(_moved(moved))
    return 0


def _stopped(moved: fast_forward.Moved) -> None:
    """The commit a rebase stopped at, the files to fix, and the two ways on."""
    at = f" at {moved.stopped_at} ({moved.subject})" if moved.stopped_at else ""
    stopped = f"{render.branch(moved.branch)} stopped rebasing onto {moved.base}{at}"
    if moved.paths:
        files = "".join(f"\n  {path}" for path in moved.paths)
        _err(render.fail(f"{stopped}, with conflicts in:{files}"))
    else:
        _err(render.fail(f"{stopped}, with no conflicts left unstaged"))
    _err(
        render.note(
            "resolve each file and git add it, then git rebase --continue; "
            f"git rebase --abort puts {render.branch(moved.branch)} back at "
            f"{moved.previous_sha}"
        )
    )


def _moved(moved: fast_forward.Moved) -> str:
    """Where HEAD stood before the run and stands after: branch, sha, note."""
    from . import fast_forward as ff
    from . import repo as R

    base = R.ref_name(moved.base)
    now: dict[str, str] = {
        ff.CURRENT: f"already on {base}",
        ff.FAST_FORWARDED: f"fast-forwarded by {_commits(moved.behind)} to {base}",
        ff.REBASED: f"rebased onto {base}",
        ff.ROTATED: f"new, from {base}",
    }
    note = now[moved.result]
    if moved.ahead:
        note += f", {_commits(moved.ahead)} of its own"
    after = (
        Cell("now", BOLD),
        Cell(moved.branch, render.BRANCH),
        Cell(moved.sha, YELLOW),
        Cell(note, DIM),
    )
    if moved.result == ff.CURRENT:
        return table([after])
    was = {
        ff.FAST_FORWARDED: "",
        ff.REBASED: f"{_commits(moved.behind)} behind {base}",
        ff.ROTATED: f"kept, finished: {moved.why}",
    }
    before = (
        Cell("was", DIM),
        Cell(moved.previous, render.BRANCH),
        Cell(moved.previous_sha, YELLOW),
        Cell(was[moved.result], DIM),
    )
    return table([before, after])


def _commits(n: int) -> str:
    return f"{n} commit" if n == 1 else f"{n} commits"


# --------------------------------------------------------------------------
# ci
# --------------------------------------------------------------------------


def _review_number(text: str) -> int:
    if not text.isdigit() or not int(text):
        raise argparse.ArgumentTypeError(f"{text!r} is not a pull request number")
    return int(text)


def _add_ci_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "number",
        nargs="?",
        type=_review_number,
        metavar="NUMBER",
        help="the pull or merge request; the current branch's without one",
    )
    p.add_argument("--json", action="store_true", help="the snapshot as data")
    p.add_argument(
        "--wait",
        action="store_true",
        help="read again every 30 seconds until no check is running, "
        "for up to 30 minutes",
    )
    p.add_argument(
        "-v", "--verbose", action="store_true", help="print every forge command"
    )


# Failures first, so the row worth reading is the one at the top.
_BUCKETS = ("fail", "cancel", "blocked", "pending", "unknown", "skipping", "pass")
_BUCKET_INK = {
    "fail": RED,
    "cancel": RED,
    "blocked": YELLOW,
    "pending": YELLOW,
    "unknown": YELLOW,
    "skipping": DIM,
    "pass": GREEN,
}


def run_ci(args: argparse.Namespace) -> int:
    from . import ci, sync
    from .git import options

    if sync.offline():
        raise Refused(f"gw ci-checks reads the forge, and {sync.OFFLINE} is set")
    options.verbose = args.verbose

    def reader() -> dict:
        snapshot = ci.read(args.number)
        running = sum(c["bucket"] == "pending" for c in snapshot["checks"])
        progress.say(f"#{snapshot['pr']['number']}: {running} running")
        return snapshot

    try:
        with progress.running(not args.verbose):
            progress.say("reading checks")
            snapshot = ci.wait(reader) if args.wait else reader()
    except ci.Unreadable as exc:
        _err(render.fail(str(exc)))
        return 1

    if snapshot["stale"]:
        _warn("the review changed while it was read; run gw ci-checks again")
    elif args.wait and snapshot["checksState"] == "pending":
        _warn("checks are still running after 30 minutes")
    if args.json:
        print(_json(snapshot))
        return 0

    pr = snapshot["pr"]
    rows: list[Row] = [
        (Cell("STATE", BOLD), Cell("CHECK", BOLD), Cell("LINK", BOLD)),
        *(
            (
                Cell(c["bucket"], _BUCKET_INK.get(c["bucket"], "")),
                Cell(" / ".join(part for part in (c["workflow"], c["name"]) if part)),
                Cell(c["link"], DIM),
            )
            for c in sorted(
                snapshot["checks"],
                key=lambda c: (_BUCKETS.index(c["bucket"]), c["workflow"], c["name"]),
            )
        ),
    ]
    if snapshot["checks"]:
        print(table(rows))
    o = render.out
    print(
        f"{o(pr['url'], BOLD)}: {o(pr['headRef'], render.BRANCH)} at "
        f"{o(pr['headSha'][:7], YELLOW)}, checks {snapshot['checksState']}"
    )
    return 0


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
    p.add_argument("--table", action="store_true", help="print them all and pick none")
    # For the shell completions, which must not hold a candidate of their own.
    # Hidden because a person has --table, which is the same answer formatted
    # for eyes.
    p.add_argument("--complete", action="store_true", help=argparse.SUPPRESS)
    _add_cd_flags(p)


def _add_remove_flags(p: argparse.ArgumentParser) -> None:
    """gw prune's rules for one target. No --only: one target has one verdict."""
    p.add_argument(
        "query", nargs="?", default="", metavar="PATH|QUERY", help="which one"
    )
    p.add_argument(
        "--keep-branch",
        action="store_true",
        help="remove the checkout and retain its branch and stashes",
    )
    _add_assess_flags(p)
    p.add_argument(
        "--table",
        action="store_true",
        help="assess without prompting; uncoloured table",
    )
    p.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="approve removal, including with --json or --table",
    )
    # Removal candidates omit main, which Git cannot remove.
    p.add_argument("--complete", action="store_true", help=argparse.SUPPRESS)


def _landed(args: argparse.Namespace, landed: wt_mod.Landed) -> int:
    """One destination on stdout, so `cd $(gw add x)` works."""

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

    if args.table or not sys.stdin.isatty():
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

    # The main checkout is offered even to somebody standing in it: from a
    # subdirectory it is the way back to the top. Any other worktree you
    # stand in is shown above the rest, marked and never highlighted.
    def offerable(w: R.Worktree) -> bool:
        return w.main or not _same(w.path, here)

    offered = [w for w in found if offerable(w)]
    if not offered:
        _err(render.note(f"already in {render.branch(found[0].label)}"))
        return 0

    # Which repository this is, above the list, so nobody has to run
    # `git remote -v` to find out. Only where the picker is drawn: --table and
    # --json are what a script reads, and a query that narrows to one takes it
    # without showing anything.
    if not (args.query and len(offered) == 1):
        for name, url in R.remote_urls():
            _err(f"{render.ref(name)}: {url}")

    # The picker holds every worktree and opens filtered by the query, so one
    # esc widens it to all of them. A query that narrows to one has said
    # which. No query has not, even when the repository holds exactly one
    # other worktree.
    everyone = [w for w in rows if offerable(w)]
    chosen = pick.choose(
        everyone,
        outright=bool(args.query),
        shown=[("*", w) for w in rows if not offerable(w)],
        here=next((w for w in everyone if _same(w.path, here)), None),
        query=args.query or "",
    )
    if chosen is None:
        _err(render.note("nothing picked"))
        return 0
    print(chosen.path)
    return 0


def _remove_preview(args: argparse.Namespace, rows: list[verdicts.Verdict]) -> int:
    """The rows gw prune would print for these worktrees, and no removal.

    Never a bare path on stdout: the gw function changes into one.
    """
    if args.json:
        print(_json({"verdicts": _verdict_data(rows)}, indent=2))
        return 0
    if args.table:
        render.out.on = False
    print(_verdict_table(rows))
    if args.quiet:
        return 0
    print()
    print(_counted(rows))
    if len(rows) == 1 and rows[0].eligible:
        sys.stdout.flush()
        named = shlex.quote(rows[0].path)
        _err(render.note(f"gw remove {named} --yes removes it"))
    return 0


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
        # finished is what gw remove works out after you pick one, not what decides
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

    # gw prune's rule: without approval, a caller that cannot answer, or asked
    # for data, gets the assessment and nothing changes.
    if not args.yes and (args.json or args.table or not sys.stdin.isatty()):
        return _remove_preview(args, candidates)
    if args.query:
        _refuse_ambiguous(found)

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
    from . import repo as R
    from . import sync

    print(__version__)
    text, same = plugin_state()
    _err(render.note(text) if same else render.warn(text))
    if R.toplevel().value:
        _err(render.note(sync.head_line(R.head())))
    return 0


def _guide() -> int:
    """Every gw command, with the shell plugin's shortcuts under each."""
    o = render.out
    print(
        f"{o('worktrees', BOLD)} {o(__version__, YELLOW)}: git worktree commands "
        "that refuse to lose work"
    )
    print()

    # The subcommand blue, what goes after it dim, a shortcut green, so each
    # kind of name reads as one thing. Plain text off a terminal.
    rows: list[Row] = [(Cell("COMMAND", BOLD), Cell("DOES", BOLD))]
    for path, c in command_rows():
        aliases = f" ({', '.join(c.aliases)})" if c.aliases else ""
        usage = f" {c.usage}" if c.usage else ""
        rows.append(
            (
                Cell.of(("gw ", DIM), (path, BLUE), (aliases, DIM), (usage, DIM)),
                Cell(c.about),
            )
        )
        for name, words in shortcuts(path, c):
            rows.append(
                (Cell.of(("  ", ""), (name, BOLD + GREEN)), Cell(" ".join(words), DIM))
            )
    print(table(rows))
    print()
    print("Indented names are shortcuts the shell plugin defines.")
    text, same = plugin_state()
    print(o(text, "" if same else YELLOW))
    return 0


def shortcuts(path: str, command: _Command) -> list[tuple[str, list[str]]]:
    """Each shortcut the shell plugins define for this command, with the
    words it runs: `gwp!` runs `gw prune --yes`."""
    return [
        (name, ["gw", *path.split(), *flags.split()])
        for name, flags in command.shortcuts.items()
    ]


def _shortcuts_epilog(path: str, command: _Command) -> str | None:
    """The end of a command's --help, naming its shortcuts."""
    found = shortcuts(path, command)
    if not found:
        return None
    return (
        "Shell plugin shortcuts: "
        + "; ".join(f"{name} runs {' '.join(words)}" for name, words in found)
        + "."
    )


def _complete_commands() -> int:
    """Every subcommand and shorthand, for the shell completions.

    Name and description, tab-separated, which is what fish reads directly
    and what zsh splits for `_describe`. It comes from the same table `gw --help`
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
        "--table",
        action="store_true",
        help="assess without prompting; uncoloured table",
    )
    p.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="approve removal, including with --json or --table",
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
        child = subs.add_parser(
            name,
            help=command.about,
            description=command.description,
            epilog=_shortcuts_epilog(f"branch {name}", command),
        )
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
    p = _parser(args.prog, _COMMANDS["branch"].description)
    _add_branch_flags(p)
    p.print_help()
    return 0


class _Command:
    """One command, every shorthand for it, and the shell plugin's shortcuts.

    Every alias reaches the row's function. Help prints these rows: `about`
    in the table `gw --help` prints, `description` at the top of the
    command's own --help.
    """

    __slots__ = (
        "about",
        "aliases",
        "description",
        "flags",
        "lands",
        "run",
        "shortcuts",
        "usage",
    )

    run: Callable[[argparse.Namespace], int]
    flags: Callable[[argparse.ArgumentParser], None] | None
    about: str
    description: str  # one or two sentences saying what the command does
    usage: str
    aliases: tuple[str, ...]
    # A shell function's name, and the flags it adds to `gw <command>`.
    shortcuts: dict[str, str]
    lands: bool  # changes the caller's directory, so the gw function cds

    def __init__(
        self,
        run: Callable[[argparse.Namespace], int],
        flags: Callable[[argparse.ArgumentParser], None] | None,
        about: str,
        description: str,
        usage: str = "",
        aliases: tuple[str, ...] = (),
        shortcuts: dict[str, str] | None = None,
        lands: bool = False,
    ) -> None:
        self.run = run
        self.flags = flags
        self.about = about
        self.description = description
        self.usage = usage
        self.aliases = aliases
        self.shortcuts = shortcuts or {}
        self.lands = lands


_BRANCH_COMMANDS: dict[str, _Command] = {
    "create": _Command(
        run_new_branch,
        _add_new_branch_flags,
        "branch off the head branch",
        "Branch NAME off the head ref and check it out. The branch has no "
        "upstream, so a push never targets the head branch.",
        usage="NAME",
        shortcuts={"gwbc": ""},
    ),
    "status": _Command(
        run_branch_status,
        _add_branch_status_flags,
        "which branches are finished, and why",
        "Assess each local branch no worktree has checked out and say whether "
        "it is finished, with the proof. Name branches to assess only those.",
        usage="[NAME ...]",
        shortcuts={"gwbs": ""},
    ),
    "prune": _Command(
        run_branch_delete,
        _add_branch_delete_flags,
        "delete local branches proved finished",
        "Delete local branches proved finished, asking first unless --yes; "
        "--dry-run reports without deleting. Name the branches, pick them on a "
        "terminal, or pass --all for every one.",
        usage="[NAME ...]",
        shortcuts={"gwbp": ""},
    ),
}


_COMMANDS: dict[str, _Command] = {
    "sync": _Command(
        run_sync,
        _add_sync_flags,
        "refresh remote refs and forge evidence",
        "Fetch every remote and its tags, and record which local branch tips "
        "the forge has merged. Every other command runs it first once the last "
        "sync is WORKTREES_SYNC_TTL seconds old, 60 by default.",
        shortcuts={"gws": ""},
    ),
    "branch": _Command(
        run_branch,
        _add_branch_flags,
        "create, assess and delete local branches",
        "Start a branch off the head ref, or assess and delete local branches "
        "no worktree has checked out.",
    ),
    "prune": _Command(
        run_prune,
        _add_prune_flags,
        "assess worktrees and remove eligible ones",
        "Assess every worktree, and on a terminal ask before removing the "
        "eligible ones. Nonterminal input, --json, --table and --only assess "
        "without removing; --yes approves removal and cannot be combined with "
        "--only.",
        aliases=("p",),
        shortcuts={
            "gwp": "",
            "gwp!": "--yes",
            "gwpa": "--delete all",
            "gwpa!": "--delete all --yes",
        },
    ),
    "add": _Command(
        run_add,
        _add_add_flags,
        "create a worktree and land you in it",
        "Create a worktree for branch NAME and change into it, checking out the "
        "branch if it exists. A new branch starts from BASE, or from the head "
        "ref without one.",
        usage="NAME [BASE]",
        aliases=("a",),
        shortcuts={"gwa": ""},
        lands=True,
    ),
    "list": _Command(
        run_list,
        _add_list_flags,
        "list worktrees or pick a destination",
        "Pick a worktree on a terminal and change into it; QUERY narrows the "
        "list, and takes the one worktree it matches outright. Without a "
        "terminal, or with --json or --table, print the matching worktrees.",
        usage="[QUERY]",
        aliases=("l",),
        shortcuts={"gwl": ""},
        lands=True,
    ),
    "remove": _Command(
        run_remove,
        _add_remove_flags,
        "remove a worktree",
        "Remove one worktree and its branch once the branch is proved finished; "
        "a terminal picks and asks, and --keep-branch keeps the branch. "
        "Nonterminal input, --json and --table assess the match without "
        "removing; --yes approves removal, except of the worktree you stand in, "
        "which needs a second answer at a terminal.",
        usage="[QUERY]",
        aliases=("rm",),
        shortcuts={"gwr": "", "gwr!": "--yes"},
        lands=True,
    ),
    "fast-forward": _Command(
        run_fast_forward,
        _add_fast_forward_flags,
        "bring this branch level with the head ref",
        "Bring the branch you stand on level with the head ref: fast-forward "
        "it, or rebase its own commits and stop at the first conflict. A branch "
        "already merged there keeps its name, and the checkout moves to a new "
        "branch started from the head ref or --from.",
        usage="[NAME]",
        aliases=("ff",),
        shortcuts={"gwff": ""},
    ),
    "ci-checks": _Command(
        run_ci,
        _add_ci_flags,
        "a pull request's checks, read from the forge",
        "Read one pull or merge request and its checks from GitHub or GitLab, "
        "without syncing first or changing anything. It exits 0 whenever it "
        "read a snapshot, failing checks included.",
        usage="[NUMBER]",
        aliases=("ci",),
    ),
    "version": _Command(
        run_version,
        None,
        "the version this package installs",
        "Print this package's version on stdout, and on stderr whether the "
        "shell plugin is the same release.",
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


class _Parser(argparse.ArgumentParser):
    """argparse, with its help on a stream the caller can see.

    A command that changes directory prints its destination on stdout, and
    the gw shell function captures that stream, so its help goes to stderr.
    The rest keep argparse's stdout, where `gw prune -h | less` reads it.
    """

    def __init__(self, *args, lands: bool = False, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.lands = lands
        # Subparsers are built from this class too and their defaults win, so
        # every command finds the parser that names it, for `_needs`.
        self.set_defaults(parser=self)

    def print_help(self, file=None) -> None:
        if file is None:
            file = sys.stderr if self.lands else sys.stdout
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


def gw(argv: list[str] | None = None) -> int:
    """The short name. Its own prog, so a usage error names what was typed."""
    return main(argv, prog="gw")


def main(argv: list[str] | None = None, prog: str = "worktrees") -> int:
    """The CLI a shim and an agent call."""
    args_in = sys.argv[1:] if argv is None else argv
    if args_in and args_in[0] == "--complete":
        return _complete_commands()
    if args_in in ([], ["-h"], ["--help"]):
        render.setup()
        return _guide()
    if args_in == ["--explain"]:
        render.setup()
        return _explain()
    if args_in[0] not in _CANONICAL:
        # A worktree is reached through `gw list`, so no name competes with a
        # command or a shorthand.
        render.setup()
        word = shlex.quote(args_in[0])
        _err(
            render.fail(
                f"{prog}: {word} is not a command\n"
                f"Run: gw list {word} to pick a worktree, or gw --help"
            )
        )
        return 2

    p = _parser(prog, "Git worktree commands that refuse to lose work.")
    subs = p.add_subparsers(dest="command")
    name = _CANONICAL[args_in[0]]
    command = _COMMANDS[name]
    sub = subs.add_parser(
        name,
        aliases=list(command.aliases),
        help=command.about,
        description=command.description,
        epilog=_shortcuts_epilog(name, command),
        lands=command.lands,
    )
    if command.flags:
        command.flags(sub)
    return _dispatch(p, args_in)


def _dispatch(p: argparse.ArgumentParser, argv: list[str]) -> int:
    render.setup()
    args = p.parse_args(argv)
    # argparse gives a subparser its own prog; rebuild it rather than reach
    # into the private table for it. The alias the caller typed, not the
    # canonical name, so a usage line echoes what they wrote.
    args.prog = f"{p.prog} {args.command}"
    if getattr(args, "branch_command", None):
        args.prog += f" {args.branch_command}"
    run = _COMMANDS[_CANONICAL[args.command]].run
    try:
        if getattr(args, "quiet", False) and getattr(args, "verbose", False):
            raise _Stop("--quiet and --verbose cannot be combined", 2)
        values = getattr(args, "delete", [])
        if getattr(args, "keep_branch", False) and any(
            value != "ignored-files" for value in values
        ):
            raise _Stop("--keep-branch accepts only --delete ignored-files", 2)
        args.permissions = frozenset(
            ("empty", "ignored-files", "stashes") if "all" in values else values
        )
        if run is run_prune and args.only and args.yes:
            raise _Stop("--only cannot be combined with --yes", 2)
        # sync and ci-checks talk to the network and assess nothing, so
        # neither syncs first, needs the offline guard or a word about how
        # fresh the evidence is.
        if (
            run not in (run_version, run_sync, run_ci)
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
            # The never-synced line is on stderr under -q and --json too: a
            # caller acting on these verdicts needs to know no sync stands
            # behind them. -q and --json hide the rest.
            hushed = getattr(args, "json", False) or getattr(args, "quiet", False)
            if R.toplevel().value:
                _refresh(args)
                shown = sync.freshness(quiet=True) if hushed else sync.notices()
                for notice in shown:
                    _err(render.note(notice))
        code = run(args)
        merged = sys.modules.get("worktrees.merged")
        quiet = getattr(args, "quiet", False) or getattr(args, "json", False)
        if merged is not None and merged.lacking.is_set() and not quiet:
            _err(
                render.warn(
                    "some verdicts are unknown: "
                    f"{merged.LACKING}, and assessment fetches none\n"
                    "git backfill fetches them"
                )
            )
        return code
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
