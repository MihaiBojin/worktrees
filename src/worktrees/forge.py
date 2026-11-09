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


def _run(
    argv: list[str], repo: str | os.PathLike[str] | None = None
) -> subprocess.CompletedProcess[str] | None:
    """Run a forge command with repository redirects removed, or None when it
    could not start or ran out of time."""
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
    return proc


def _execute(
    argv: list[str], repo: str | os.PathLike[str] | None = None
) -> subprocess.CompletedProcess[str] | None:
    """Run a forge command, or None when it did not succeed."""
    proc = _run(argv, repo)
    return proc if proc is not None and proc.returncode == 0 else None


def authenticated(
    tool: str, host: str, repo: str | os.PathLike[str] | None = None
) -> bool:
    proc = _execute([tool, "auth", "status", "--hostname", host], repo)
    return proc is not None


def _json(text: str) -> object:
    import json

    try:
        return json.loads(text or "[]")
    except json.JSONDecodeError:
        return None


def _read(argv: list[str], repo: str | os.PathLike[str] | None = None) -> object:
    proc = _execute(argv, repo)
    return None if proc is None else _json(proc.stdout)


def pages(
    argv: list[str], repo: str | os.PathLike[str] | None = None
) -> list[dict] | None:
    """Read the JSON arrays a `--paginate` call prints back to back, as one."""
    import json

    proc = _execute(argv, repo)
    if proc is None:
        return None
    decoder, text, at, rows = json.JSONDecoder(), proc.stdout, 0, []
    try:
        while True:
            while at < len(text) and text[at].isspace():
                at += 1
            if at == len(text):
                break
            page, at = decoder.raw_decode(text, at)
            if not isinstance(page, list):
                return None
            rows.extend(page)
    except json.JSONDecodeError:
        return None
    if not rows and not text.strip():
        return None
    return rows if all(isinstance(row, dict) for row in rows) else None


def query(
    argv: list[str], repo: str | os.PathLike[str] | None = None
) -> list[dict] | None:
    """Read a list of forge records."""
    return _records(_read(argv, repo))


def _records(rows: object) -> list[dict] | None:
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        return None
    return rows


def commit_query(
    argv: list[str], repo: str | os.PathLike[str] | None = None
) -> list[dict] | None:
    """Read a list of forge records about one commit, empty when the forge
    answers that it holds no such commit, as for one nobody has pushed.

    GitHub answers that with a 422 and GitLab with a 404, each with a body
    that says so. Any other failure is None.
    """
    proc = _run(argv, repo)
    if proc is None:
        return None
    if proc.returncode == 0:
        return _records(_json(proc.stdout))
    body = _json(proc.stdout)
    message = body.get("message") if isinstance(body, dict) else None
    unknown = isinstance(message, str) and (
        message.startswith("No commit found for SHA: ")
        or message == "404 Commit Not Found"
    )
    return [] if unknown else None


def document(
    argv: list[str], repo: str | os.PathLike[str] | None = None
) -> dict | None:
    """Read one authoritative repository record."""
    row = _read(argv, repo)
    return row if isinstance(row, dict) else None
