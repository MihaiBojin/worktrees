"""Branch command arguments, reports, and confirmation."""

from __future__ import annotations

import argparse
import sys

from . import branches, progress, prune, render
from .cli import _assessment_head, _err, _explain, _json, _only, _Stop, add_status_flags
from .git import GitError, Refused, options
from .render import BOLD, DIM, YELLOW, Cell, table


def status_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "names",
        nargs="*",
        metavar="NAME",
        help="exact local branch names; all when omitted",
    )
    _assessment_flags(p)
    add_status_flags(p)


def _assessment_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--json", action="store_true", help="verdicts and evidence as data")
    p.add_argument("-q", "--quiet", action="store_true", help="omit the summary")
    p.add_argument("-v", "--verbose", action="store_true", help="print every command")
    p.add_argument(
        "--explain", action="store_true", help="print every git command and exit"
    )
    p.add_argument("--complete", action="store_true", help=argparse.SUPPRESS)


def delete_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "names",
        nargs="*",
        metavar="NAME",
        help="exact local branch names; opens a selector when omitted",
    )
    _assessment_flags(p)
    p.add_argument(
        "--all", action="store_true", help="assess all local branches for deletion"
    )
    p.add_argument("--dry-run", action="store_true", help="report without deleting")
    p.add_argument(
        "-y", "--yes", action="store_true", help="skip deletion confirmation"
    )


def _prepare(
    args: argparse.Namespace,
) -> tuple[str, str, str, str, list[branches.Verdict]] | None:
    options.verbose = args.verbose
    if args.complete:
        for name, (sha, _) in branches.local_refs().items():
            print(f"{name}\t{sha[:12]}")
        return None
    if args.explain:
        _explain()
        return None
    # A terminal spinner reports the local branch checks.
    with progress.running(not args.quiet and not args.verbose):
        head, head_branch, remote = _assessment_head(args)
        head_sha = prune.sha_of(head).out.strip() if head else ""
        progress.say("reading local branches")
        try:
            rows = branches.assess(args.names, head_sha, head_branch, remote, True)
        except ValueError as exc:
            raise _Stop(str(exc), 2) from exc
    return head, head_sha, head_branch, remote, rows


def _table(rows: list[branches.Verdict]) -> str:
    return table(
        [
            (
                Cell("VERDICT", BOLD),
                Cell("BRANCH", BOLD),
                Cell("SHA", BOLD),
                Cell("WHY", BOLD),
                Cell("WORKTREE", BOLD),
            ),
            *[
                (
                    _verdict(v),
                    Cell(v.branch, render.BRANCH),
                    Cell(v.sha[:12], YELLOW),
                    Cell(v.why),
                    Cell(", ".join(v.paths), DIM),
                )
                for v in rows
            ],
        ]
    )


def _report(
    args: argparse.Namespace,
    head: str,
    head_sha: str,
    rows: list[branches.Verdict],
    **outcomes: object,
) -> None:
    if args.json:
        print(
            _json(
                {
                    "head": head,
                    "head_sha": head_sha,
                    "verdicts": [v.data() for v in _only(args, rows)],
                    **outcomes,
                },
                indent=2,
            )
        )
    else:
        print(_table(_only(args, rows)))
        if not args.quiet:
            # Every row counted, --only or not, so a filtered table still
            # says what it is not showing.
            print("\n" + render.counted([v.verdict for v in rows]))


def _branches(n: int) -> str:
    return f"{n} branch" if n == 1 else f"{n} branches"


def _verdict(v: branches.Verdict) -> Cell:
    return Cell(v.verdict, render.VERDICT.get(v.verdict, ""))


def _plan(go: list[branches.Verdict]) -> str:
    """What is about to be deleted, on stderr, before the question."""
    return table(
        [
            (
                Cell("VERDICT", BOLD),
                Cell("BRANCH", BOLD),
                Cell("SHA", BOLD),
                Cell("WHY", BOLD),
            ),
            *[
                (
                    _verdict(v),
                    Cell(v.branch, render.BRANCH),
                    Cell(v.sha[:12], YELLOW),
                    Cell(v.why),
                )
                for v in go
            ],
        ],
        ink=render.err,
    )


def status(args: argparse.Namespace) -> int:
    prepared = _prepare(args)
    if prepared is None:
        return 0
    head, head_sha, _, _, rows = prepared
    _report(args, head, head_sha, rows)
    return int(any(v.error for v in rows))


def _select(rows: list[branches.Verdict]) -> list[branches.Verdict]:
    candidates = [v for v in rows if v.verdict == "remove"]
    if not candidates:
        return []
    if not sys.stdin.isatty():
        raise Refused("not a terminal; pass branch names or --all, or use gwbs --json")
    # The ones that cannot be picked first and grey, so the numbered rows
    # sit together above the prompt.
    others = [v for v in rows if v.verdict != "remove"]
    _err(
        table(
            [
                (
                    Cell("", BOLD),
                    Cell("VERDICT", BOLD),
                    Cell("BRANCH", BOLD),
                    Cell("SHA", BOLD),
                    Cell("WHY", BOLD),
                ),
                *[
                    (
                        Cell("-", DIM),
                        # In its colour even here: keep and unknown are
                        # different reasons not to offer it.
                        _verdict(v),
                        Cell(v.branch, DIM),
                        Cell(v.sha[:12], DIM),
                        Cell(v.why, DIM),
                    )
                    for v in others
                ],
                *[
                    (
                        Cell(str(i), BOLD),
                        _verdict(v),
                        Cell(v.branch, render.BRANCH),
                        Cell(v.sha[:12], YELLOW),
                        Cell(v.why),
                    )
                    for i, v in enumerate(candidates, 1)
                ],
            ],
            ink=render.err,
        )
    )
    answer = prune._prompt(
        render.ask(
            "which?",
            f"[1-{len(candidates)}, space-separated numbers, or blank to cancel]",
        )
    ).strip()
    if not answer:
        return []
    parts = answer.split()
    if any(not p.isdecimal() or not 1 <= int(p) <= len(candidates) for p in parts):
        raise Refused("choose numbers from the list, separated by spaces")
    return [candidates[i - 1] for i in dict.fromkeys(map(int, parts))]


def delete(args: argparse.Namespace) -> int:
    if args.all and args.names:
        raise _Stop("--all cannot be combined with branch names", 2)
    prepared = _prepare(args)
    if prepared is None:
        return 0
    head, head_sha, head_branch, remote, rows = prepared
    if not head_sha:
        from . import repo as R

        raise Refused(R.head().unresolved)
    if args.names or args.all or args.dry_run:
        go = [v for v in rows if v.verdict == "remove"]
    else:
        go = _select(rows)
    removed: list[str] = []
    failures: list[dict[str, str]] = []
    if args.dry_run:
        _report(
            args, head, head_sha, rows, removed=removed, failed=failures, dry_run=True
        )
        if not args.json:
            # After the table, even when stdout is a pipe that buffers it.
            sys.stdout.flush()
            _err(
                render.note(
                    f"dry run: {_branches(len(go))} would go; nothing was deleted"
                )
            )
        return int(any(v.error for v in rows))
    if go and not args.json:
        _err(render.note(f"deleting {_branches(len(go))}:"))
        _err(_plan(go))
    if go and not args.yes:
        if not sys.stdin.isatty():
            raise Refused("not a terminal; pass --yes to confirm deletion")
        if prune._prompt(
            render.ask(f"delete {_branches(len(go))}?", "[y/N]")
        ).strip().lower() not in ("y", "yes"):
            _report(args, head, head_sha, rows, removed=removed, failed=failures)
            return 0
    # Each one is judged again before it goes, which costs what assessing it
    # did the first time.
    with progress.running(not args.quiet and not args.verbose):
        for i, row in enumerate(go, 1):
            progress.say(
                f"{row.branch}: checking again, then deleting ({i} of {len(go)})"
            )
            try:
                branches.delete(row, head, head_sha, head_branch, remote, True)
            except (GitError, Refused, ValueError) as exc:
                failures.append({"branch": row.branch, "why": str(exc)})
                _err(render.fail(f"{render.branch(row.branch)} kept: {exc}"))
            else:
                removed.append(row.branch)
    if args.json:
        _report(args, head, head_sha, rows, removed=removed, failed=failures)
    elif removed:
        names = ", ".join(render.out(name, render.BRANCH) for name in removed)
        print(f"deleted {_branches(len(removed))}: {names}")
    if removed:
        # One block, after the fact, and on stderr even under --json: only
        # what actually went needs a way back, and a person always sees it.
        _err(render.note("each comes back with:"))
        for row in go:
            if row.branch in removed:
                _err(f"  {render.restore(row.branch, row.sha)}")
    # A named branch that was not proved finished says why, rather than
    # leaving exit 3 to speak for it.
    held = [v for v in rows if args.names and v.verdict != "remove"]
    for v in held:
        _err(render.fail(f"{render.branch(v.branch)} kept: {v.why}"))
    if failures or any(v.error for v in rows):
        return 1
    return 3 if held else 0
