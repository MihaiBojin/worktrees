"""Run explicit forge queries with repository redirects removed."""

from __future__ import annotations

import os
import subprocess

from .git import options

REDIRECTS = ("GH_REPO", "GITLAB_REPO")


def _trace(argv: list[str], code: int, stdout: str, stderr: str) -> None:
    if options.verbose:
        from . import progress, render

        progress.line(render.trace(argv, code, stdout, stderr, failed=code != 0))


def _execute(
    argv: list[str], repo: str | os.PathLike[str] | None = None
) -> subprocess.CompletedProcess[str] | None:
    """Run a forge command with repository redirects removed."""
    options.log.append(tuple(argv))
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            check=False,
            cwd=os.fspath(repo) if repo else None,
            env={k: v for k, v in os.environ.items() if k not in REDIRECTS},
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _trace(argv, 1, "", str(exc))
        return None
    _trace(argv, proc.returncode, proc.stdout, proc.stderr)
    if proc.returncode != 0:
        return None

    return proc


def authenticated(
    tool: str, host: str, repo: str | os.PathLike[str] | None = None
) -> bool:
    proc = _execute([tool, "auth", "status", "--hostname", host], repo)
    return proc is not None


def _read(argv: list[str], repo: str | os.PathLike[str] | None = None) -> object:
    import json

    proc = _execute(argv, repo)
    if proc is None:
        return None
    try:
        rows = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return None
    return rows


def query(
    argv: list[str], repo: str | os.PathLike[str] | None = None
) -> list[dict] | None:
    """Read a list of forge records."""
    rows = _read(argv, repo)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        return None
    return rows


def document(
    argv: list[str], repo: str | os.PathLike[str] | None = None
) -> dict | None:
    """Read one authoritative repository record."""
    row = _read(argv, repo)
    return row if isinstance(row, dict) else None
