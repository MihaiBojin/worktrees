"""Branch command arguments, reports, and confirmation."""

from __future__ import annotations

import argparse
import json
import sys

from . import branches, prune
from .cli import _assessment_head, _err, _explain, _Stop
from .git import GitError, Refused, options
from .render import BOLD, DIM, Cell, table


def status_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "names",
        nargs="*",
        metavar="NAME",
        help="exact local branch names; all when omitted",
    )
    _assessment_flags(p)


def _assessment_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--no-fetch", action="store_true", help="use the refs already here")
    p.add_argument("--no-forge", action="store_true", help="never ask GitHub or GitLab")
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
    head, head_branch, remote = _assessment_head(args)
    head_sha = prune.sha_of(head).out.strip()
    if not head_sha:
        raise GitError(f"cannot resolve {head}")
    try:
        rows = branches.assess(
            args.names, head_sha, head_branch, remote, not args.no_forge
        )
    except ValueError as exc:
        raise _Stop(str(exc), 2) from exc
    return head, head_sha, head_branch, remote, rows


def _table(rows: list[branches.Verdict]) -> str:
    return table(
        [
            (
                Cell("VERDICT", DIM),
                Cell("BRANCH", DIM),
                Cell("SHA", DIM),
                Cell("WHY", DIM),
                Cell("WORKTREE", DIM),
            ),
            *[
                (
                    Cell(v.verdict),
                    Cell(v.branch, BOLD),
                    Cell(v.sha[:12]),
                    Cell(v.why),
                    Cell(", ".join(v.paths)),
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
            json.dumps(
                {
                    "head": head,
                    "head_sha": head_sha,
                    "verdicts": [v.data() for v in rows],
                    **outcomes,
                },
                indent=2,
            )
        )
    else:
        print(_table(rows))
        if not args.quiet:
            counts = {
                name: sum(v.verdict == name for v in rows)
                for name in ("remove", "keep", "unknown")
            }
            print(
                f"\n{counts['remove']} removable, {counts['keep']} kept, "
                f"{counts['unknown']} unclear"
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
    for row in rows:
        if row.verdict != "remove":
            _err(f"  -  {row.branch}: {row.why}")
    for i, row in enumerate(candidates, 1):
        _err(f"  {i}  {row.branch}  {row.why}")
    answer = prune._prompt(
        f"which? [1-{len(candidates)}, space-separated numbers, or blank to cancel] "
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
    if args.names or args.all or args.dry_run:
        go = [v for v in rows if v.verdict == "remove"]
    else:
        go = _select(rows)
    for row in go:
        _err(f"{row.branch} at {row.sha}: {row.why}")
        _err(f"  undo: {row.undo}")
    removed: list[str] = []
    failures: list[dict[str, str]] = []
    if args.dry_run:
        _report(
            args, head, head_sha, rows, removed=removed, failed=failures, dry_run=True
        )
        return int(any(v.error for v in rows))
    if go and not args.yes:
        if not sys.stdin.isatty():
            raise Refused("not a terminal; pass --yes to confirm deletion")
        if prune._prompt(
            f"delete {len(go)} branch(es)? [y/N] "
        ).strip().lower() not in ("y", "yes"):
            _report(args, head, head_sha, rows, removed=removed, failed=failures)
            return 0
    for row in go:
        try:
            branches.delete(row, head, head_sha, head_branch, remote, not args.no_forge)
        except (GitError, Refused, ValueError) as exc:
            failures.append({"branch": row.branch, "why": str(exc)})
            _err(f"{row.branch} kept: {exc}")
        else:
            removed.append(row.branch)
    _report(args, head, head_sha, rows, removed=removed, failed=failures)
    if removed and not args.json:
        print("deleted: " + ", ".join(removed))
    if failures or any(v.error for v in rows):
        return 1
    return 3 if args.names and any(v.verdict != "remove" for v in rows) else 0
