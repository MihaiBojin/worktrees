"""Ask a forge CLI a question about this repository.

`branch_forge` builds the question; this runs it and reads the answer.
"""

from __future__ import annotations

import os
import subprocess

from .git import options

# `gh` reads `GH_REPO` and `glab` reads `GITLAB_REPO`, and either beats the
# directory the CLI is run in. A variable left in a shell would send every
# question to a repository nobody here consulted, and branch names repeat
# across repositories: a merged request found under the wrong one reads as
# proof and deletes a branch.
REDIRECTS = ("GH_REPO", "GITLAB_REPO")


class Unanswered(Exception):
    """The forge was asked and gave no answer to read; the message says why."""


def _trace(argv: list[str], code: int, stdout: str, stderr: str) -> None:
    if options.verbose:
        from . import progress, render

        progress.line(render.trace(argv, code, stdout, stderr, failed=code != 0))


def query(argv: list[str]) -> list[dict]:
    """Read a forge response, with repository redirects removed.

    Raises Unanswered when the CLI cannot run, fails, or prints something
    other than a list of objects.
    """
    import json

    options.log.append(tuple(argv))
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            check=False,
            env={k: v for k, v in os.environ.items() if k not in REDIRECTS},
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _trace(argv, 1, "", str(exc))
        raise Unanswered(str(exc)) from exc
    _trace(argv, proc.returncode, proc.stdout, proc.stderr)
    if proc.returncode != 0:
        said = proc.stderr.strip().splitlines()
        raise Unanswered(said[0] if said else f"{argv[0]} exited {proc.returncode}")

    try:
        rows = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise Unanswered(f"{argv[0]} printed something other than JSON") from exc
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise Unanswered(f"{argv[0]} printed something other than a list")
    return rows
