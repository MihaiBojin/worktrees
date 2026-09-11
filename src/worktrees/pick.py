"""Choosing one worktree out of the handful this repository has.

No fzf. The largest number of linked worktrees in one repository here is
four, and at that size a numbered prompt reads faster than a fuzzy finder
and costs no dependency, no spawn, no tty rules and no absent-fzf fallback.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence

from . import render
from .git import Refused
from .repo import Worktree


def subsequence(query: str, text: str) -> tuple[int, int] | None:
    """The one idea worth taking from fzf: the query's letters in order.

    Returns how tightly they cluster and where they start, so `tst` finds
    `add-tests`. None when they do not all appear.
    """
    q, t = query.lower(), text.lower()
    first: int | None = None
    last = seen = 0
    for i, ch in enumerate(t):
        if seen < len(q) and ch == q[seen]:
            if first is None:
                first = i
            last, seen = i, seen + 1
    if seen < len(q) or first is None:
        return None
    return last - first, first


def matches(query: str, worktrees: Sequence[Worktree]) -> list[Worktree]:
    """Substring first, then subsequence, each group in its own order.

    A substring hit always beats a subsequence one, so typing more of a name
    never moves it down the list.

    Substring looks at the branch and the path; subsequence looks at the
    branch alone. Every path here contains `.worktrees`, which supplies a
    `t`, a `w` and an `o` to any query that wants them, so a loose match
    against the path finds everything and means nothing.
    """
    if not query:
        return list(worktrees)
    q = query.lower()
    exact: list[tuple[int, Worktree]] = []
    loose: list[tuple[int, Worktree]] = []
    for wt in worktrees:
        label, path = wt.label.lower(), wt.path.lower()
        if q in label:
            exact.append((label.index(q), wt))
            continue
        if q in path:
            exact.append((len(label) + path.index(q), wt))
            continue
        rank = subsequence(query, wt.label)
        if rank is not None:
            loose.append((rank[0], wt))
    return [wt for _, wt in exact] + [wt for _, wt in loose]


def choose(
    candidates: Sequence[Worktree],
    show: Callable[[str], None],
    ask: Callable[[str], str] | None = None,
) -> Worktree | None:
    """One match takes it outright. Otherwise ask, and None means cancelled.

    A run whose stdin is not a terminal is refused rather than left to block:
    an agent or a pipe reaching a prompt would hang, and --json answers the
    same question without one.
    """
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    if ask is None:
        if not sys.stdin.isatty():
            raise Refused(
                f"{len(candidates)} worktrees match and this is not a terminal; "
                "narrow the query, or use --list or --json"
            )
        ask = _prompt

    width = max(len(wt.label) for wt in candidates)
    for i, wt in enumerate(candidates, 1):
        show(
            f"{render.err(f'{i:>3}', render.DIM)}  "
            f"{render.err(wt.label.ljust(width), render.BOLD)}  "
            f"{render.err(wt.path, render.DIM)}"
        )
    answer = ask(f"which? [1-{len(candidates)}, or blank to cancel] ").strip()
    if not answer:
        return None
    if not answer.isdigit() or not 1 <= int(answer) <= len(candidates):
        raise Refused(f"{answer} is not one of 1 to {len(candidates)}")
    return candidates[int(answer) - 1]


def _prompt(text: str) -> str:
    """The question on stderr, the answer from stdin, so stdout stays data."""
    sys.stderr.write(text)
    sys.stderr.flush()
    return sys.stdin.readline()
